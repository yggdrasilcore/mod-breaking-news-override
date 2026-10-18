/*
 * Copyright (C) 2016+ YggdrasilCore <yggdrasilcore.github.io>, released under GNU AGPL v3 license: https://github.com/yggdrasilcore/yggdrasilcore/blob/master/LICENSE
 */

// From SC
void AddBreakingNewsScripts();

// Add all
// cf. the naming convention https://github.com/azerothcore/azerothcore-wotlk/blob/master/doc/changelog/master.md#how-to-upgrade-4
// additionally replace all '-' in the module folder name with '_' here
void Addmod_breaking_news_overrideScripts()
{
    AddBreakingNewsScripts();
}
