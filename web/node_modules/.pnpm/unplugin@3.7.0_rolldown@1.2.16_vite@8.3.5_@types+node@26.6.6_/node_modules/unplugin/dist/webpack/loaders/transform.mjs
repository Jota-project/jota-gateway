import { a as normalizeObjectHook, i as unescapeResourcePath } from "../../webpack-like-BSTzHRun.mjs";
import { n as createBuildContext, r as createContext } from "../../context-VOLsP74b.mjs";
import { t as mergeSourceMaps } from "../../sourcemap-BGvAAfVS.mjs";
// src/webpack/loaders/transform.ts
const filters = /* @__PURE__ */ new WeakMap();
const raw = true;
async function transform(source, map) {
	const callback = this.async();
	const { plugin } = this.query;
	if (!plugin?.transform) return callback(null, source, map);
	const id = unescapeResourcePath(this.resource, "webpack");
	const context = createContext(this);
	const hook = plugin.transform;
	const cwd = process.cwd();
	let cached = filters.get(hook);
	let handler;
	if (cached?.cwd === cwd) handler = typeof hook === "function" ? hook : hook.handler;
	else {
		const normalized = normalizeObjectHook("transform", hook);
		handler = normalized.handler;
		cached = {
			cwd,
			filter: normalized.filter
		};
		filters.set(hook, cached);
	}
	const { filter } = cached;
	const code = typeof source === "string" ? source : source.toString("utf-8");
	if (!filter(id, code)) return callback(null, source, map);
	try {
		const res = await handler.call(Object.assign({}, createBuildContext({
			addWatchFile: (file) => {
				this.addDependency(file);
			},
			getWatchFiles: () => {
				return this.getDependencies();
			}
		}, this._compiler, this._compilation, this, map), context), code, id);
		if (res == null) callback(null, source, map);
		else if (typeof res !== "string") callback(null, res.code, this.sourceMap === false ? map : mergeSourceMaps(res.map, map, id));
		else callback(null, res, map);
	} catch (error) {
		if (error instanceof Error) callback(error);
		else callback(new Error(String(error)));
	}
}
export { transform as default, raw };
