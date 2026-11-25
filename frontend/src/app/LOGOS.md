# Lab logos

`lab-logos.tsx` draws each house model's lab logo on the player tiles (home page replay, Watch page, season video).

- Source: [`@lobehub/icons-static-svg`](https://github.com/lobehub/lobe-icons) version 1.102.0 (published 2026-11-02), MIT licence.
- The files are used unaltered: the generator drops only the outer `<svg>` element and its `<title>`, and keeps the 24×24 view box.
- The marks are trademarks of their owners. They are shown only to say whose model is playing; Agent Survivor is not affiliated with any lab, and the site footer and the video end card say so.
- To remove a lab's logo, delete its entry from `LAB_MARKS`; its players go back to the generated robot.

| Model id prefix | Lab | File |
|---|---|---|
| `anthropic` | Anthropic | `anthropic.svg` |
| `openai` | OpenAI | `openai.svg` |
| `google` | Google | `google-color.svg` |
| `x-ai` | xAI | `xai.svg` |
| `meta`, `meta-llama` | Meta | `meta-color.svg` |
| `qwen` | Qwen (Alibaba) | `qwen-color.svg` |
| `deepseek` | DeepSeek | `deepseek-color.svg` |
| `moonshotai` | Moonshot AI | `moonshot.svg` |
| `z-ai` | Z.ai | `zai.svg` |
| `mistralai` | Mistral AI | `mistral-color.svg` |
| `cohere` | Cohere | `cohere-color.svg` |
| `minimax` | MiniMax | `minimax-color.svg` |
| `nvidia` | NVIDIA | `nvidia-color.svg` |
| `microsoft` | Microsoft | `microsoft-color.svg` |
| `amazon` | Amazon | `aws-color.svg` |
| `ibm-granite` | IBM | `ibm.svg` |
| `rekaai` | Reka | `reka.svg` |
| `tencent` | Tencent | `tencent-color.svg` |

No logo on file (generated robot): `inclusionai`, any other prefix, and every submitted agent (`entry/…`).

To regenerate: download the package (`npm pack @lobehub/icons-static-svg@<version>`), unpack it in a scratch folder, and run `python3 -I scripts/gen_lab_logos.py <folder>/package/icons frontend/src/app/lab-logos.tsx`.
