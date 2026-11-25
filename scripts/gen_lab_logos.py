"""Build frontend/src/app/lab-logos.tsx from lobe-icons SVG files, unaltered.

Usage: python3 -I scripts/gen_lab_logos.py <unpacked package>/icons frontend/src/app/lab-logos.tsx
"""
import json, re, sys
icons, out = sys.argv[1], sys.argv[2]
# model-id prefix -> (lab name, lobe-icons file)
LABS = [
    ("anthropic", "Anthropic", "anthropic"),
    ("openai", "OpenAI", "openai"),
    ("google", "Google", "google-color"),
    ("x-ai", "xAI", "xai"),
    ("meta", "Meta", "meta-color"),
    ("meta-llama", "Meta", "meta-color"),
    ("qwen", "Qwen (Alibaba)", "qwen-color"),
    ("deepseek", "DeepSeek", "deepseek-color"),
    ("moonshotai", "Moonshot AI", "moonshot"),
    ("z-ai", "Z.ai", "zai"),
    ("mistralai", "Mistral AI", "mistral-color"),
    ("cohere", "Cohere", "cohere-color"),
    ("minimax", "MiniMax", "minimax-color"),
    ("nvidia", "NVIDIA", "nvidia-color"),
    ("microsoft", "Microsoft", "microsoft-color"),
    ("amazon", "Amazon", "aws-color"),
    ("ibm-granite", "IBM", "ibm"),
    ("rekaai", "Reka", "reka"),
    ("tencent", "Tencent", "tencent-color"),
]
marks = {}
for prefix, name, file in LABS:
    svg = open(f"{icons}/{file}.svg").read()
    assert not re.search(r"<script|href=|url\(http|\son[a-z]+=", svg, re.I), file
    assert 'viewBox="0 0 24 24"' in svg, file
    inner = re.sub(r"^<svg[^>]*>|</svg>\s*$", "", svg.strip())
    inner = re.sub(r"<title>.*?</title>", "", inner)
    mono = "currentColor" in svg
    marks[prefix] = {"name": name, "file": f"{file}.svg", "mono": mono, "markup": inner}
body = json.dumps(marks, indent=1, ensure_ascii=False)
open(out, "w").write(f'''/*
 * Lab logos for the player tiles, keyed by the part of the model id before "/".
 *
 * Generated from @lobehub/icons-static-svg 1.102.0 (MIT), files unaltered except
 * that the outer <svg> and <title> are dropped. See LOGOS.md for sources. The
 * marks belong to their owners; they say whose model is playing, nothing more.
 * Regenerate rather than edit by hand. Remove a lab to give it back its robot.
 */

export type LabMark = {{
  /** the lab's name, for the tile's accessible title */
  name: string;
  /** the lobe-icons file it came from */
  file: string;
  /** true when the mark is drawn in currentColor (one colour) */
  mono: boolean;
  /** SVG markup on a 24 by 24 grid, from a vetted static file (never user input) */
  markup: string;
}};

export const LAB_MARKS: Record<string, LabMark> = {body};

/** The lab mark for a model id such as "anthropic/claude-sonnet-5.5", or null. */
export function labMark(modelId?: string | null): LabMark | null {{
  const prefix = (modelId ?? "").split("/", 1)[0];
  return Object.prototype.hasOwnProperty.call(LAB_MARKS, prefix) ? LAB_MARKS[prefix] : null;
}}
''')
print(len(marks), "marks")
