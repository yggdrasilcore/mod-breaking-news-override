/*
 * Copyright (C) 2016+ YggdrasilCore <yggdrasilcore.github.io>, released under GNU AGPL v3 license: https://github.com/yggdrasilcore/yggdrasilcore/blob/master/LICENSE
 */

#include "BreakingNews.h"

#include <cstdio>
#include <mutex>

// Guards bn_Enabled/bn_Title/bn_Body/bn_Formatted: read on every
// char-enum packet (session threads), written on config reload.
static std::mutex bn_Mutex;

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

std::vector<std::string> BreakingNewsServerScript::GetChunks(std::string s, uint8_t chunkSize)
{
    std::vector<std::string> chunks;

    for (uint32_t i = 0; i < s.size(); i += chunkSize)
        chunks.push_back(s.substr(i, chunkSize));

    return chunks;
}

void BreakingNewsServerScript::SendChunkedPayload(Warden* warden, WardenPayloadMgr* payloadMgr, std::string payload, uint32 chunkSize)
{
    bool verbose = sConfigMgr->GetOption<bool>("BreakingNews.Verbose", false);

    auto chunks = GetChunks(payload, chunkSize);

    if (!payloadMgr->GetPayloadById(_prePayloadId))
        payloadMgr->RegisterPayload(_prePayload, _prePayloadId);

    payloadMgr->QueuePayload(_prePayloadId);
    warden->ForceChecks();

    if (verbose)
        LOG_INFO("module", "Sent pre-payload '{}'.", _prePayload);

    for (auto const& chunk : chunks)
    {
        auto smallPayload = "wlbuf = wlbuf .. \"" + EscapeLuaString(chunk, '"') + "\";";
    
        payloadMgr->RegisterPayload(smallPayload, _tmpPayloadId, true);
        payloadMgr->QueuePayload(_tmpPayloadId);
        warden->ForceChecks();

        if (verbose)
            LOG_INFO("module", "Sent mid-payload '{}'.", smallPayload);
    }

    if (!payloadMgr->GetPayloadById(_postPayloadId))
        payloadMgr->RegisterPayload(_postPayload, _postPayloadId);

    payloadMgr->QueuePayload(_postPayloadId);
    warden->ForceChecks();

    if (verbose)
        LOG_INFO("module", "Sent post-payload '{}'.", _postPayload);
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
        SendChunkedPayload(warden, payloadMgr, formatted, 128);
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
