import type { ProjectFileInput } from "./types";

const ignoredParts = new Set([".git", ".venv", "venv", "node_modules", "__pycache__", ".idea",
  ".pytest_cache", ".mypy_cache", ".ruff_cache", "work", "__macosx", ".ssh", ".aws", ".azure", ".kube", ".gnupg", ".docker"]);
const sensitiveNames = new Set([".mcp.json", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
  ".pgpass", ".my.cnf", ".vault-token", "credentials", "credentials.json", "secrets.json", "secrets.yaml",
  "secrets.yml", "secrets.toml", "application_default_credentials.json", "service-account.json",
  "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "kubeconfig"]);

export function ignoredProjectPath(path: string): boolean {
  const parts = path.toLowerCase().split("/");
  const name = parts.at(-1) || "";
  return parts.some(part => ignoredParts.has(part)) || name === ".env" || name.startsWith(".env.")
    || sensitiveNames.has(name) || /\.(pem|key|p12|pfx|jks|keystore)$/.test(name);
}

export async function fileBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(binary);
}

export async function readProjectFolder(selected: File[]): Promise<{ name: string; files: ProjectFileInput[]; skipped: number }> {
  const files: ProjectFileInput[] = [];
  let size = 0, skipped = 0;
  const root = selected[0]?.webkitRelativePath.split("/")[0] || "";
  if (!root) throw new Error("浏览器未提供文件夹相对路径，请改用 ZIP 上传。");
  if (ignoredProjectPath(root)) throw new Error("请选择项目文件夹，不能上传凭据、依赖或缓存目录。");
  const candidates = selected.map(file => {
    if (!file.webkitRelativePath.startsWith(root + "/")) throw new Error("请选择单个项目文件夹。");
    return { file, path: file.webkitRelativePath.slice(root.length + 1) };
  }).filter(item => {
    if (ignoredProjectPath(item.path)) { skipped++; return false; }
    return true;
  });
  if (!candidates.length || candidates.length > 1000) throw new Error("项目需包含 1–1000 个文件，依赖、缓存和凭据不参与上传。");
  for (const { file } of candidates) {
    size += file.size;
    if (file.size > 2_000_000 || size > 20_000_000) throw new Error("单文件最多 2 MB，项目总大小最多 20 MB。");
  }
  for (const { file, path } of candidates.sort((a, b) => a.path.localeCompare(b.path))) {
    files.push({ path, content_base64: await fileBase64(file) });
  }
  return { name: root, files, skipped };
}
