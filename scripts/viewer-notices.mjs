import { readFileSync, writeFileSync } from "node:fs";
const parts = ["three", "lucide"].map(name => `${name}\n${"=".repeat(name.length)}\n${readFileSync(`node_modules/${name}/LICENSE`, "utf8")}`);
writeFileSync("voicedesign/static/THIRD_PARTY_LICENSES.txt", parts.join("\n\n"));
