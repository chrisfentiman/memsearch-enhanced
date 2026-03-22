# Changelog

## [0.1.6](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.5...v0.1.6) (2026-03-22)


### Features

* initial release — enhanced Stop hook, multi-query recall, insights skill ([6211722](https://github.com/chrisfentiman/memsearch-enhanced/commit/62117222f3f57637ba631f1b2a46604d7457cf08))
* semantic router with self-improving exemplars ([#1](https://github.com/chrisfentiman/memsearch-enhanced/issues/1)) ([ca3f74a](https://github.com/chrisfentiman/memsearch-enhanced/commit/ca3f74ac0accbd73b8fd5f361525ba6a3c40bb2b))
* strip preamble before first category marker, retry up to 3 times ([7617a05](https://github.com/chrisfentiman/memsearch-enhanced/commit/7617a0593aedbe28da9ca52f3d3b09e6e91abc7c))


### Bug Fixes

* prevent haiku from responding to transcript as conversation ([f69dfb2](https://github.com/chrisfentiman/memsearch-enhanced/commit/f69dfb2f94bc3d50f9ccf3a7c08e0f9bdd17d3d4))
* resolve PLUGIN_ROOT for prompt file, bundle parse-transcript.sh, add full lifecycle hooks ([a4c3913](https://github.com/chrisfentiman/memsearch-enhanced/commit/a4c3913f83b3604885bc6df84382adb79a8b4ef2))
* shorter positive-only system prompt, add data markers to examples ([a3ea59a](https://github.com/chrisfentiman/memsearch-enhanced/commit/a3ea59ad7dfb737c7a499a8563c4a0c0a47bfbc7))
* stronger no-preamble constraint for haiku, bump to 0.1.1 ([3388c59](https://github.com/chrisfentiman/memsearch-enhanced/commit/3388c593772c65860cb0c976b8bbd6853845bcda))
* use grep+tail for preamble stripping (macOS sed compatible) ([4541e7f](https://github.com/chrisfentiman/memsearch-enhanced/commit/4541e7f088e18de428092efdebc461018406e905))
* wrap transcript with data markers, place instruction after data ([5391c66](https://github.com/chrisfentiman/memsearch-enhanced/commit/5391c66cdae025078e990760c04012c1f1f379ad))
