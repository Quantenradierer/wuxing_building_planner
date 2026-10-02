// Schattenakte: imports roomplanner buildings (.schattenakte.json) as one scene with a
// level per floor. The file holds Foundry scene data plus the floor images (base64 WebP);
// the images go to Data/schattenakte/<name>/, the levels are pointed at them.

const FORMAT = "schattenakte";
const FORMAT_VERSION = 1;
const ROOT = "schattenakte";

Hooks.on("renderSceneDirectory", (app, html) => {
  if (!game.user.isGM) return;
  const footer = html.querySelector(".directory-footer");
  if (!footer || footer.querySelector(".schattenakte-import")) return;
  const button = document.createElement("button");
  button.type = "button";
  button.classList.add("schattenakte-import");
  button.innerHTML = '<i class="fa-solid fa-building"></i> Import building';
  button.addEventListener("click", () => pickFile());
  footer.append(button);
});

function pickFile() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".json";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      await importBuilding(JSON.parse(await file.text()));
    } catch (error) {
      console.error("Schattenakte |", error);
      ui.notifications.error(`Schattenakte: ${error.message ?? error}`);
    }
  });
  input.click();
}

async function importBuilding(data) {
  if (data?.format !== FORMAT) throw new Error("not a .schattenakte.json file");
  if (data.version > FORMAT_VERSION) {
    throw new Error("the file is newer than this module; please update Schattenakte");
  }
  const scene = foundry.utils.deepClone(data.scene);
  const existing = game.scenes.get(scene._id);
  if (existing) {
    const replace = await foundry.applications.api.DialogV2.confirm({
      window: { title: "Import building" },
      content: `<p><strong>${Handlebars.escapeExpression(existing.name)}</strong> was imported before. Replace it?</p>`,
    });
    if (!replace) return;
  }

  const progress = ui.notifications.info(`Importing ${data.name}…`, { progress: true });
  const source = uploadSource();
  const folder = `${ROOT}/${data.name}`;
  await ensureFolder(source, ROOT);
  await ensureFolder(source, folder);
  const files = Object.entries(data.images);
  for (const [index, [name, base64]] of files.entries()) {
    const file = new File([decode(base64)], name, { type: "image/webp" });
    await filePicker().upload(source, folder, file, {}, { notify: false });
    progress.update({ pct: (index + 1) / (files.length + 1) });
  }
  for (const level of scene.levels) level.background.src = `${folder}/${level.background.src}`;

  if (existing) await existing.delete();
  const created = await Scene.create(scene, { keepId: true });
  progress.update({ pct: 1 });
  try {
    const { thumb } = await created.createThumbnail();
    await created.update({ thumb });
  } catch (error) {
    console.warn("Schattenakte | no thumbnail", error);
  }
  ui.notifications.info(`Imported ${created.name} (${scene.levels.length} floors).`);
  await created.view();
}

function filePicker() {
  return foundry.applications.apps.FilePicker.implementation;
}

function uploadSource() {
  return globalThis.ForgeVTT?.usingTheForge ? "forgevtt" : "data";
}

async function ensureFolder(source, path) {
  try {
    await filePicker().createDirectory(source, path, {});
  } catch (error) {
    // Already there (the only expected failure); a real problem shows up in the upload.
  }
}

function decode(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
