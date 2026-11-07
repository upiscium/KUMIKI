/**
 * KUMIKI #364 Phase A: *unactivated* typed Git inspection prototype.
 *
 * DO NOT grant this tool to agents or substitute it for a publication gate
 * until later phases qualify its Git/Task authority and whitespace semantics.
 * No Git command text, Justfile, root path, or arbitrary option is model input.
 */
import { tool } from "@opencode-ai/plugin";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, realpathSync, readFileSync } from "node:fs";
import { isAbsolute, join, sep } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const HOST_CONFIG = "/etc/kagari/safe-git-inspection-host.json";
const MAX_BINARY_BYTES = 64 * 1024 * 1024;
const MAX_RESULT_BYTES = 1536;

function hostOwned(path: string, directory: boolean): void {
  const info = lstatSync(path);
  if (info.isSymbolicLink() || info.uid !== 0 ||
      (info.mode & 0o022) !== 0 ||
      (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1) ||
      realpathSync(path) !== path) {
    throw new Error("Host source authority has unsafe metadata");
  }
}

function checkedAncestorDirectories(path: string): void {
  // File ownership alone is insufficient when an ancestor directory can be
  // renamed by a lower-trust principal after digest verification.
  let current = "/";
  hostOwned(current, true);
  for (const segment of path.split("/").filter(Boolean).slice(0, -1)) {
    current = join(current, segment);
    hostOwned(current, true);
  }
}

function configuredPrograms(): { python: string; git: string } {
  // The location is fixed in reviewed tool source, not the caller's PATH or
  // environment. The root-owned host provision is a separate Human gate.
  checkedAncestorDirectories(HOST_CONFIG);
  for (const part of ["/etc", "/etc/kagari"]) hostOwned(part, true);
  hostOwned(HOST_CONFIG, false);
  const raw = readFileSync(HOST_CONFIG, "utf8");
  if (Buffer.byteLength(raw) > 4096) throw new Error("Host pin configuration is oversized");
  const config = JSON.parse(raw);
  if (config.schema_version !== 1 || !config.git || !config.python ||
      Object.keys(config).sort().join(",") !== "git,python,schema_version") {
    throw new Error("Host pin configuration has an invalid schema");
  }
  function pinned(entry: { path: string; sha256: string }): string {
    if (!entry || typeof entry.path !== "string" || !isAbsolute(entry.path) ||
        typeof entry.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(entry.sha256)) {
      throw new Error("Host executable is not pinned");
    }
    const path = entry.path;
    checkedAncestorDirectories(path);
    const info = lstatSync(path);
    if (info.isSymbolicLink() || !info.isFile() || info.uid !== 0 ||
        (info.mode & 0o022) !== 0 || info.nlink !== 1 ||
        info.size > MAX_BINARY_BYTES || info.size === 0 ||
        realpathSync(path) !== path) {
      throw new Error("Host executable has unsafe metadata");
    }
    const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
    if (actual !== entry.sha256) {
      throw new Error("Host executable digest does not match the installed pin");
    }
    return path;
  }
  return { python: pinned(config.python), git: pinned(config.git) };
}

function checkedWorkspaceAncestors(root: string): void {
  // A different account must not be able to rename the inspected Task root
  // between script validation and child execution. This does not defend
  // against a fully compromised same-UID process or an adversarial mount.
  let current = "/";
  const owner = typeof process.getuid === "function" ? process.getuid() : -1;
  for (const entry of ["", ...root.split("/").filter(Boolean)]) {
    if (entry) current = join(current, entry);
    const info = lstatSync(current);
    if (!info.isDirectory() || info.isSymbolicLink() ||
        (info.mode & 0o022) !== 0 ||
        (info.uid !== 0 && info.uid !== owner) ||
        realpathSync(current) !== current) {
      throw new Error("Task root has an untrusted ancestor directory");
    }
  }
}

function checkedSource(root: string): string {
  checkedWorkspaceAncestors(root);
  const components = [join(root, ".automation"), join(root, ".automation/bin")];
  const script = join(components[1], "safe_git_inspect.py");
  for (const path of [...components, script]) {
    const info = lstatSync(path);
    const directory = components.includes(path);
    if (info.isSymbolicLink() || (info.mode & 0o022) !== 0 ||
        (typeof process.getuid === "function" && info.uid !== process.getuid()) ||
        (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1) ||
        realpathSync(path) !== path) {
      throw new Error("Git inspection source has unsafe metadata");
    }
  }
  return script;
}

export default tool({
  description: "Experimental read-only Task-bound Git identity / index metadata. PHASE A ONLY; not a publication check.",
  args: {
    task: tool.schema.string().regex(/^[1-9][0-9]{0,8}$/),
    operation: tool.schema.enum(["identity", "index-meta"]),
  },
  async execute(args, context) {
    if (!context.worktree || !context.directory) {
      throw new Error("Canonical Worktree context is unavailable");
    }
    const root = realpathSync(context.worktree);
    const session = realpathSync(context.directory);
    if (session !== root && !session.startsWith(root + sep)) {
      throw new Error("Session is not inside the authorized Task Worktree");
    }
    const script = checkedSource(root);
    const { python, git } = configuredPrograms();
    // Explicit environment: no inherited Git, credential, Python or loader knobs.
    const env = {
      PATH: python.slice(0, python.lastIndexOf("/")) + ":" +
            git.slice(0, git.lastIndexOf("/")) + ":/usr/bin:/bin",
      HOME: "/nonexistent",
      XDG_CONFIG_HOME: "/nonexistent",
      PYTHONNOUSERSITE: "1",
      PYTHONDONTWRITEBYTECODE: "1",
      KAGARI_GIT_INSPECT_ROOT: root,
      KAGARI_GIT_INSPECT_BINARY: git,
    };
    const { stdout } = await execFileAsync(
      python, ["-I", "-B", script, args.task, args.operation],
      {
        cwd: root, env, timeout: 24000, maxBuffer: 4096,
        shell: false, encoding: "utf8", windowsHide: true,
      },
    );
    if (!stdout.endsWith("\n") || Buffer.byteLength(stdout) > MAX_RESULT_BYTES) {
      throw new Error("Git inspection response exceeded the protocol boundary");
    }
    const value = JSON.parse(stdout);
    const rootHash = createHash("sha256").update(root).digest("hex");
    if (value.schema_version !== 1 || value.phase !== "A" ||
        value.qualification !== "PROTOTYPE_ONLY" || value.task !== args.task ||
        value.operation !== args.operation || value.worktree_sha256 !== rootHash) {
      throw new Error("Git inspection response identity mismatch");
    }
    return stdout.trimEnd();
  },
});
