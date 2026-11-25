# applemint-marketplace（影片製作版）

This is a video-focused copy of [applemint-ltd/applemint-marketplace](https://github.com/applemint-ltd/applemint-marketplace). 這是上游 marketplace 的影片製作精簡版，只保留與影片製作相關的 Claude Code plugin。

上游目前也只有這一個 plugin（`ad-video-tools`），因此內容與上游相同，沒有刪除其他 plugin。安裝來源改為本倉庫。

| Plugin | What it does / 用途 |
|---|---|
| [ad-video-tools](plugins/ad-video-tools) | Rebuild an ad in an existing video's exact layout with your footage, copy, language and brand, or cut a short from a plan and a folder of raw footage. 可量測既有廣告影片的版面與剪輯節奏並依樣重建，或依企劃／分鏡從素材剪出短影音（含旁白、字幕、SRT）。 |

Skills in `ad-video-tools`:

- **init-video** — 為客戶建立影片專案資料夾、品牌設定與環境檢查。
- **new-video** — 在專案中開一支新影片，放入企劃或參考片與素材後交給剪輯技能。
- **video-style-clone** — 分析既有參考影片的版面、構圖與節奏（量到像素），存成範本，再用你的素材、文案、語言與品牌重建。
- **video-edit** — 依企劃或分鏡從素材剪出 9:16／4:5／1:1 短影音，含配音（TTS）、字幕、SRT 與品管報告。開頭 hook 是剪輯裡的字幕樣式（前 3 秒），不是「從既有影片內容產出多種 hook 腳本」的技能。

## Install（Claude Code）

上游 README 只說明 Claude Code 的安裝方式；本倉庫沿用同一組指令，來源改為本帳號：

```
/plugin marketplace add AndyJuang/applemint-marketplace
/plugin install ad-video-tools@applemint-marketplace
```

上游安裝來源是 `applemint-ltd/applemint-marketplace`。

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 applemint Ltd. This repository keeps the original license and copyright notice. Source: [applemint-ltd/applemint-marketplace](https://github.com/applemint-ltd/applemint-marketplace).
