# Changelog

## [0.1.12](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.11...v0.1.12) (2026-03-22)


### Features

* version check for classifier daemon on session start ([#15](https://github.com/chrisfentiman/memsearch-enhanced/issues/15)) ([2960361](https://github.com/chrisfentiman/memsearch-enhanced/commit/2960361d2ce8d1e55f9625641ef8fec60afc7e4b))

## [0.1.11](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.10...v0.1.11) (2026-03-22)


### Bug Fixes

* improve category inference in self-improvement loop ([#13](https://github.com/chrisfentiman/memsearch-enhanced/issues/13)) ([0981ece](https://github.com/chrisfentiman/memsearch-enhanced/commit/0981ecef407aa24c28f24a79018818e28fc9fd1c))

## [0.1.10](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.9...v0.1.10) (2026-03-22)


### Bug Fixes

* make SessionEnd hook async with 120s timeout ([#11](https://github.com/chrisfentiman/memsearch-enhanced/issues/11)) ([1ea7f0d](https://github.com/chrisfentiman/memsearch-enhanced/commit/1ea7f0dbd778c23a285cfcfcde220d420b5b2336))

## [0.1.9](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.8...v0.1.9) (2026-03-22)


### Bug Fixes

* skip system/XML prompts in UserPromptSubmit hook ([#9](https://github.com/chrisfentiman/memsearch-enhanced/issues/9)) ([c21e284](https://github.com/chrisfentiman/memsearch-enhanced/commit/c21e2843010da44aa3e238e22282d2fe6476e782))

## [0.1.8](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.7...v0.1.8) (2026-03-22)


### Features

* expand bootstrap exemplars to 2052 with compile script ([#7](https://github.com/chrisfentiman/memsearch-enhanced/issues/7)) ([213a869](https://github.com/chrisfentiman/memsearch-enhanced/commit/213a869c7d8a24b4f4fb6fafadae5347aa17ff16))

## [0.1.7](https://github.com/chrisfentiman/memsearch-enhanced/compare/v0.1.6...v0.1.7) (2026-03-22)


### Bug Fixes

* use CLAUDE_PROJECT_DIR instead of undefined CWD in UserPromptSubmit hook ([#4](https://github.com/chrisfentiman/memsearch-enhanced/issues/4)) ([42c838d](https://github.com/chrisfentiman/memsearch-enhanced/commit/42c838dadf2d01981c24545109ddf7186a496cb5))

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
