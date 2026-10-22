/*
 * Copyright (C) 2016+ YggdrasilCore <yggdrasilcore.github.io>, released under GNU AGPL v3 license: https://github.com/yggdrasilcore/yggdrasilcore/blob/master/LICENSE
 */

#include "BreakingNews.h"

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <string>
#include <vector>

// Guards bn_Enabled/bn_Title/bn_Body/bn_Formatted: read on every
// char-enum packet (session threads), written on config reload.
static std::mutex bn_Mutex;

static const std::string _midPayloadFmt =
    "local a=ServerAlertFrame;local b=ServerAlertText;local c=ServerAlertTitle;local d=CharacterSelect;"
    "if a~=nil and b~=nil and c~=nil and d~=nil then "
    "a:SetParent(d)ServerAlertTitle:SetText('{}')ServerAlertText:SetText('{}')a:Show()"
    "else message('ServerAlert(Frame|Text|Title)/CharacterSelect Frame is nil.')end";

static std::string EscapeLuaString(std::string const& value, char quote)
{
    std::string out;
    out.reserve(value.size());

    for (char c : value)
    {
        unsigned char byte = static_cast<unsigned char>(c);
        if (c == '\\' || c == quote)
        {
            out.push_back('\\');
            out.push_back(c);
        }
        else if (byte < 0x20 || byte == 0x7F)
        {
            char escape[8];
            std::snprintf(escape, sizeof(escape), "\\%03u", static_cast<unsigned>(byte));
            out += escape;
        }
        else
        {
            out.push_back(c);
        }
    }

    return out;
}

// Split the raw payload so that each chunk's escaped Lua string fits within
// maxEscaped bytes. Escaping happens per chunk, so escape sequences can never
// be split across two chunks.
static std::vector<std::string> GetChunks(std::string const& s, size_t maxEscaped)
{
    std::vector<std::string> chunks;
    std::string current;
    size_t escapedLen = 0;

    for (char c : s)
    {
        unsigned char byte = static_cast<unsigned char>(c);
        size_t cost = (c == '\\' || c == '"') ? 2 : (byte < 0x20 || byte == 0x7F) ? 4 : 1;

        if (escapedLen + cost > maxEscaped && !current.empty())
        {
            chunks.push_back(current);
            current.clear();
            escapedLen = 0;
        }

        current.push_back(c);
        escapedLen += cost;
    }

    if (!current.empty())
        chunks.push_back(current);

    return chunks;
}

bool TryReadFile(std::string& path, std::string& bn_Result)
{
    std::ifstream bn_File(path);

    std::string bn_Buffer = "";
    bn_Result = "";

    if (!bn_File.is_open())
        return false;

    while (std::getline(bn_File, bn_Buffer))
        bn_Result = bn_Result + (bn_Buffer);

    bn_Result.erase(std::remove(bn_Result.begin(), bn_Result.end(), '\r'), bn_Result.cend());
    bn_Result.erase(std::remove(bn_Result.begin(), bn_Result.end(), '\n'), bn_Result.cend());

    return true;
}

bool TryReadNews(std::string& bn_Result)
{
    std::string path = sConfigMgr->GetOption<std::string>("BreakingNews.HtmlPath", "./Updates.html");
    {
        std::lock_guard<std::mutex> lock(bn_Mutex);
        bn_Title = sConfigMgr->GetOption<std::string>("BreakingNews.Title", "Breaking News");
    }

    if (path == "")
    {
        LOG_ERROR("module", "Failed to read 'BreakingNews.HtmlPath'.");
        return false;
    }

    if (!TryReadFile(path, bn_Result))
    {
        LOG_ERROR("module", "Failed to read file '{}'.", path);
        return false;
    }

    return true;
}

void BreakingNewsServerScript::SendChunkedPayload(Warden* warden, WardenPayloadMgr* payloadMgr, std::string const& payload)
{
    bool verbose = sConfigMgr->GetOption<bool>("BreakingNews.Verbose", false);

    // Start with a conservative escaped-chunk size and grow it only if the
    // payload would otherwise need more chunk ids than we have available.
    constexpr size_t minEscapedChunk = 140;
    constexpr size_t maxEscapedChunk = 148;

    size_t escapedChunkSize = minEscapedChunk;
    auto chunks = GetChunks(payload, escapedChunkSize);

    while (chunks.size() > _maxChunkCount && escapedChunkSize < maxEscapedChunk)
        chunks = GetChunks(payload, ++escapedChunkSize);

    if (chunks.empty() || chunks.size() > _maxChunkCount)
    {
        LOG_ERROR("module", "Breaking news payload is too large ({} chunks, max {}).", chunks.size(), _maxChunkCount);
        return;
    }

    static std::atomic<uint32_t> generation{0};
    uint32_t const gen = ++generation;
    std::string const genStr = std::to_string(gen);
    std::string const countStr = std::to_string(chunks.size());

    // The generation keeps chunks from different sends isolated. A stale chunk
    // from an older send writes into its own generation table and is ignored
    // by the current post-payload.
    std::string const prePayload =
        "if type(wlbuf)~='table'then wlbuf={}end;wlbuf[" + genStr + "]=wlbuf[" + genStr + "]or{};";

    std::string const postPayload =
        "local t=wlbuf[" + genStr + "];if not t then return end;local s='';for i=1," + countStr +
        " do local c=t[i];if not c then message('BN:incomplete')return end;s=s..c end;wlbuf[" + genStr +
        "]=nil;local a,b=loadstring(s);if not a then message(b)else a()end";

    if (prePayload.size() > 255 || postPayload.size() > 255)
    {
        LOG_ERROR("module", "Breaking news control payload is too large (pre {}, post {}).", prePayload.size(), postPayload.size());
        return;
    }

    // Remove only this module's pending payloads. Do not clear the whole
    // Warden queue: AIO and other modules may have payloads waiting.
    payloadMgr->DequeuePayload(_prePayloadId);
    payloadMgr->DequeuePayload(_postPayloadId);
    for (uint16 id = _chunkPayloadIdBase; id <= _chunkPayloadIdMax; ++id)
        payloadMgr->DequeuePayload(id);

    if (!payloadMgr->RegisterPayload(prePayload, _prePayloadId, true))
    {
        LOG_ERROR("module", "Failed to register breaking news pre-payload.");
        return;
    }

    payloadMgr->QueuePayload(_prePayloadId);
    warden->ForceChecks();

    if (verbose)
        LOG_INFO("module", "Sent pre-payload '{}'.", prePayload);

    uint16 chunkId = _chunkPayloadIdBase;
    uint32 chunkIndex = 1;

    for (auto const& chunk : chunks)
    {
        std::string const smallPayload =
            "if type(wlbuf)~='table'then wlbuf={}end;wlbuf[" + genStr + "]=wlbuf[" + genStr + "]or{};wlbuf[" +
            genStr + "][" + std::to_string(chunkIndex) + "]=\"" + EscapeLuaString(chunk, '"') + "\";";

        if (smallPayload.size() > 255)
        {
            LOG_ERROR("module", "Breaking news chunk {} is too large ({} bytes).", chunkIndex, smallPayload.size());
            return;
        }

        if (!payloadMgr->RegisterPayload(smallPayload, chunkId, true))
        {
            LOG_ERROR("module", "Failed to register breaking news chunk {}.", chunkIndex);
            return;
        }

        payloadMgr->QueuePayload(chunkId);
        warden->ForceChecks();

        if (verbose)
            LOG_INFO("module", "Sent mid-payload '{}'.", smallPayload);

        ++chunkId;
        ++chunkIndex;
    }

    if (!payloadMgr->RegisterPayload(postPayload, _postPayloadId, true))
    {
        LOG_ERROR("module", "Failed to register breaking news post-payload.");
        return;
    }

    payloadMgr->QueuePayload(_postPayloadId);
    warden->ForceChecks();

    if (verbose)
        LOG_INFO("module", "Sent post-payload '{}'.", postPayload);
}

void LoadBreakingNews()
{
    std::string title = sConfigMgr->GetOption<std::string>("BreakingNews.Title", "Breaking News");

    std::string body;
    if (!TryReadNews(body))
    {
        LOG_ERROR("module", "Failed to read breaking news.");
        return;
    }

    std::lock_guard<std::mutex> lock(bn_Mutex);
    bn_Title = title;
    bn_Body = body;
    bn_Formatted = Ycore::StringFormat(_midPayloadFmt,
        EscapeLuaString(bn_Title, '\''),
        EscapeLuaString(bn_Body, '\''));
}

bool BreakingNewsServerScript::CanPacketSend(WorldSession* session, WorldPacket& packet)
{
    bool enabled;
    std::string formatted;
    {
        std::lock_guard<std::mutex> lock(bn_Mutex);
        enabled = bn_Enabled;
        formatted = bn_Formatted;
    }
    if (!enabled)
        return true;

    if (packet.GetOpcode() == SMSG_CHAR_ENUM)
    {
        WardenWin* warden = (WardenWin*)session->GetWarden();
        if (!warden)
            return true;

        // Trying to use Warden before it has initialized,
        // so we exit.
        if (!warden->IsInitialized())
            return true;

        if (formatted == "")
            return true;


        auto payloadMgr = warden->GetPayloadMgr();
        if (!payloadMgr)
            return true;

        // Load in the updated news into the cache.
        if (!sConfigMgr->GetOption<bool>("BreakingNews.Cache", false))
            LoadBreakingNews();

        // The client truncates warden packets to around 256 and our payload may be larger than that.
        SendChunkedPayload(warden, payloadMgr, formatted);
    }

    return true;
}

void BreakingNewsWorldScript::OnAfterConfigLoad(bool /*reload*/)
{
    bool enabled = sConfigMgr->GetOption<bool>("BreakingNews.Enable", false);
    {
        std::lock_guard<std::mutex> lock(bn_Mutex);
        bn_Enabled = enabled;
    }

    if (!enabled)
        return;

    LoadBreakingNews();
}

// Add all scripts in one.
void AddBreakingNewsScripts()
{
    new BreakingNewsWorldScript();
    new BreakingNewsServerScript();
}
