import { i as unescapeResourcePath, n as normalizeAbsolutePath } from "../../webpack-like-BSTzHRun.mjs";
import { n as createBuildContext, r as createContext } from "../../context-VOLsP74b.mjs";
// src/webpack/loaders/load.ts
const raw = true;
async function load(source, map) {
	const callback = this.async();
	const { plugin } = this.query;
	let id = this.resource;
	if (!plugin?.load || !id) return callback(null, source, map);
	if (id.startsWith(plugin.__virtualModulePrefix)) id = decodeURIComponent(id.slice(plugin.__virtualModulePrefix.length));
	else id = unescapeResourcePath(id, "webpack");
	const context = createContext(this);
	const hook = plugin.load;
	const handler = typeof hook === "function" ? hook : hook.handler;
	try {
		const res = await handler.call(Object.assign({}, createBuildContext({
			addWatchFile: (file) => {
				this.addDependency(file);
			},
			getWatchFiles: () => {
				return this.getDependencies();
			}
		}, this._compiler, this._compilation, this), context), normalizeAbsolutePath(id));
		if (res == null) callback(null, source, map);
		else if (typeof res !== "string") callback(null, res.code, res.map ?? map);
		else callback(null, res, map);
	} catch (error) {
		if (error instanceof Error) callback(error);
		else callback(new Error(String(error)));
	}
}
export { load as default, raw };
