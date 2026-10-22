#ifndef MODULE_BREAKINGNEWS_H
#define MODULE_BREAKINGNEWS_H

#include "Config.h"
#include "Player.h"
#include "ScriptMgr.h"
#include "WardenWin.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <string>

bool bn_Enabled;
std::string bn_Title;
std::string bn_Body;
std::string bn_Formatted;

constexpr uint16 _prePayloadId = 9500;
constexpr uint16 _postPayloadId = 9501;
constexpr uint16 _chunkPayloadIdBase = 9502;
constexpr uint16 _chunkPayloadIdMax = 9999;
constexpr uint32 _maxChunkCount = _chunkPayloadIdMax - _chunkPayloadIdBase + 1;

class BreakingNewsServerScript : ServerScript
{
public:
    BreakingNewsServerScript() : ServerScript("BreakingNewsServerScript", {
        SERVERHOOK_CAN_PACKET_SEND
    }) { }

private:
    bool CanPacketSend(WorldSession* session, WorldPacket& packet) override;
    void SendChunkedPayload(Warden* warden, WardenPayloadMgr* payloadMgr, std::string const& payload);
};

class BreakingNewsWorldScript : public WorldScript
{
public:
    BreakingNewsWorldScript() : WorldScript("BreakingNewsWorldScript", {
        WORLDHOOK_ON_AFTER_CONFIG_LOAD
    }) { }

private:
    void OnAfterConfigLoad(bool reload) override;
};

#endif //MODULE_BREAKINGNEWS_H
