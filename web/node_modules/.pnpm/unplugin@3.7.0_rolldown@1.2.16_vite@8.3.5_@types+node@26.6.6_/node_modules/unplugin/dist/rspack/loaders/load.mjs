import { i as unescapeResourcePath, n as normalizeAbsolutePath } from "../../webpack-like-BSTzHRun.mjs";
import { n as createContext, t as createBuildContext } from "../../context-CR8OxsIs.mjs";
import { i as isVirtualModuleId, n as decodeVirtualModuleId } from "../../utils-FDqLgfZL.mjs";
// src/rspack/loaders/load.ts
const raw = true;
async function load(source, map) {
	const callback = this.async();
	const { plugin } = this.query;
	let id = this.resource;
	if (!plugin?.load || !id) return callback(null, source, map);
	if (isVirtualModuleId(id, plugin)) id = decodeVirtualModuleId(id, plugin);
	else id = unescapeResourcePath(id, "rspack");
	const context = createContext(this);
	const hook = plugin.load;
	const handler = typeof hook === "function" ? hook : hook.handler;
	try {
		const res = await handler.call(Object.assign({}, this._compilation && createBuildContext(this._compiler, this._compilation, this), context), normalizeAbsolutePath(id));
		if (res == null) callback(null, source, map);
		else if (typeof res !== "string") callback(null, res.code, res.map ?? map);
		else callback(null, res, map);
	} catch (error) {
		if (error instanceof Error) callback(error);
		else callback(new Error(String(error)));
	}
}
export { load as default, raw };
