<p align="center">
  <img alt="Yggdrasilcore" src="https://github.com/user-attachments/assets/6f96adf9-1867-4ca6-b627-1bd0ab7ee494" />
</p>

# Breaking News Override

`mod-breaking-news-override` is a [Yggdrasilcore](https://github.com/yggdrasilcore/yggdrasilcore) module that shows the Breaking News frame on the left side of the character-selection screen. It is a Yggdrasilcore-compatible fork of AzerothCore's [mod-breaking-news-override](https://github.com/azerothcore/mod-breaking-news-override).

The module uses the Warden Payload Manager to push an HTML body you define to the client, which then renders it on the character-selection screen. Point it at an HTML file in the configuration and the client picks it up on login.

<p align="center">
  <a href="https://github.com/yggdrasilcore/mod-breaking-news-override/actions/workflows/core-build.yml"><img src="https://github.com/yggdrasilcore/mod-breaking-news-override/actions/workflows/core-build.yml/badge.svg" alt="Build"></a>
</p>

Read more about Yggdrasil WoW and see Yggdrasilcore in action [here](https://yggdrasilwow.com/).

## Setup

1. Create an HTML file containing the body of your breaking-news frame.
2. Point the module at it in `conf/breakingnews.conf.dist`:

   ```
   BreakingNews.Enable = 1
   BreakingNews.Title = "Breaking News"
   BreakingNews.HtmlPath = "./breakingnews.html"
   BreakingNews.Cache = 1
   BreakingNews.Verbose = 0
   ```

3. Enable the module and log in to verify it works.

Example body:

```html
<html>
<body>
    <p>Welcome to Yggdrasil WoW!</p>
</body>
</html>
```

## Notes

- Only a small subset of HTML tags is supported by the client. See the [SimpleHTML reference](https://wowwiki-archive.fandom.com/wiki/UIOBJECT_SimpleHTML).
- The characters `[`, `]`, `'` and `\` must be escaped in the title and body because of the way the payload is sent. For example: `\'You think this hurts? Just wait.\'`

## What we changed

- Thread safety: the shared breaking-news payload cache (enabled flag, title, body and formatted payload) is now guarded by a mutex, since it is read on every character-enum packet across session threads and written on config reload.
- Build: Ubuntu 24.04 CI against Yggdrasilcore.
- Generated documentation for the module source.

## Documentation

- [Generated module documentation](https://yggdrasilcore.github.io/mod-breaking-news-override/)
- [AzerothCore installation guide](https://www.azerothcore.org/wiki/installation)

## Support

This module is a downstream fork and we do not provide official public support for it, and upstream channels do not support fork-specific behaviour. If you use it and find a bug, feel free to open an issue or a pull request here.

## Contributing

This repository is privately managed. We do not recommend that emulation enthusiasts follow it. Instead, we recommend that enthusiasts contribute to the upstream [mod-breaking-news-override](https://github.com/azerothcore/mod-breaking-news-override) project and to [AzerothCore](https://github.com/azerothcore/azerothcore-wotlk).

## Acknowledgements

- [azerothcore/mod-breaking-news-override](https://github.com/azerothcore/mod-breaking-news-override)
- The AzerothCore community, and Foe in particular.

## License

- Source: This repository is distributed under the MIT License.
- Intellectual property: Yggdrasil WoW and its derivative projects are not related to Blizzard Entertainment and lay no claim to Blizzard Entertainment's copyrights or intellectual property. They operate solely as an avenue for exploring the functionality of the abandonware WotLK 3.3.5a client in an educational capacity.
