import { isAbsolute, normalize, resolve } from "node:path";
import picomatch from "picomatch";
// src/utils/parse.ts
let parseImpl;
function parse(code, opts = {}) {
	if (!parseImpl) throw new Error("Parse implementation is not set. Please call setParseImpl first.");
	return parseImpl(code, opts);
}
function setParseImpl(customParse) {
	parseImpl = customParse;
}
// src/utils/general.ts
function toArray(array) {
	array = array || [];
	if (Array.isArray(array)) return array;
	return [array];
}
// src/utils/filter.ts
const BACKSLASH_REGEX = /\\/g;
function normalize$1(path) {
	return path.replace(BACKSLASH_REGEX, "/");
}
const ABSOLUTE_PATH_REGEX = /^(?:\/|(?:[A-Z]:)?[/\\|])/i;
function isAbsolute$1(path) {
	return ABSOLUTE_PATH_REGEX.test(path);
}
const GLOB_METACHARS_REGEX = /[\\^$*+?.()|[\]{}]/g;
function getMatcherString(glob, cwd) {
	if (glob.startsWith("**") || isAbsolute$1(glob)) return normalize$1(glob);
	return `${normalize$1(resolve(cwd)).replace(GLOB_METACHARS_REGEX, "\\$&")}/${normalize$1(glob).replace(/^\.\//, "")}`;
}
function patternToIdFilter(pattern) {
	if (pattern instanceof RegExp) return (id) => {
		const normalizedId = normalize$1(id);
		const result = pattern.test(normalizedId);
		pattern.lastIndex = 0;
		return result;
	};
	const glob = getMatcherString(pattern, process.cwd());
	const matcher = picomatch(glob, { dot: true });
	return (id) => {
		const normalizedId = normalize$1(id);
		return matcher(normalizedId);
	};
}
function patternToCodeFilter(pattern) {
	if (pattern instanceof RegExp) return (code) => {
		const result = pattern.test(code);
		pattern.lastIndex = 0;
		return result;
	};
	return (code) => code.includes(pattern);
}
function createFilter(exclude, include) {
	if (!exclude && !include) return;
	return (input) => {
		if (exclude?.some((filter) => filter(input))) return false;
		if (include?.some((filter) => filter(input))) return true;
		return !(include && include.length > 0);
	};
}
function normalizeFilter(filter) {
	if (typeof filter === "string" || filter instanceof RegExp) return { include: [filter] };
	if (Array.isArray(filter)) return { include: filter };
	return {
		exclude: filter.exclude ? toArray(filter.exclude) : void 0,
		include: filter.include ? toArray(filter.include) : void 0
	};
}
function createIdFilter(filter) {
	if (!filter) return;
	const { exclude, include } = normalizeFilter(filter);
	const excludeFilter = exclude?.map(patternToIdFilter);
	const includeFilter = include?.map(patternToIdFilter);
	return createFilter(excludeFilter, includeFilter);
}
function createCodeFilter(filter) {
	if (!filter) return;
	const { exclude, include } = normalizeFilter(filter);
	const excludeFilter = exclude?.map(patternToCodeFilter);
	const includeFilter = include?.map(patternToCodeFilter);
	return createFilter(excludeFilter, includeFilter);
}
function createFilterForId(filter) {
	const filterFunction = createIdFilter(filter);
	return filterFunction ? (id) => !!filterFunction(id) : void 0;
}
function createFilterForTransform(idFilterFunction, codeFilter) {
	const codeFilterFunction = createCodeFilter(codeFilter);
	if (!idFilterFunction && !codeFilterFunction) return;
	return (id, code) => {
		let fallback = true;
		if (idFilterFunction) fallback &&= idFilterFunction(id);
		if (!fallback) return false;
		if (codeFilterFunction) fallback &&= codeFilterFunction(code);
		return fallback;
	};
}
const URL_POSTFIX_REGEX = /[?#].*$/;
const QUERY_REGEX = /\?([^#]*)/;
function compileExpression(expr, hook) {
	switch (expr.kind) {
		case "and": {
			const args = expr.args.map((arg) => compileExpression(arg, hook));
			return (input) => args.every((arg) => arg(input));
		}
		case "or": {
			const args = expr.args.map((arg) => compileExpression(arg, hook));
			return (input) => args.some((arg) => arg(input));
		}
		case "not": {
			const arg = compileExpression(expr.expr, hook);
			return (input) => !arg(input);
		}
		case "id":
		case "importerId": {
			if (expr.kind === "importerId" && hook !== "resolveId") throw new Error(`[unplugin] The \`importerId\` filter can only be used with the \`resolveId\` hook, but it was used with the \`${hook}\` hook.`);
			if (expr.kind === "id" && hook === "resolveId" && typeof expr.pattern === "string") throw new Error("[unplugin] A string `id` filter is not supported for the `resolveId` hook, because its `id` is the import specifier rather than a resolved path. Use a RegExp instead.");
			const match = patternToIdFilter(expr.pattern);
			const { cleanUrl } = expr.params;
			const key = expr.kind === "id" ? "id" : "importer";
			return (input) => {
				const value = input[key];
				return value !== void 0 && match(cleanUrl ? value.replace(URL_POSTFIX_REGEX, "") : value);
			};
		}
		case "code": {
			const match = patternToCodeFilter(expr.pattern);
			return (input) => input.code !== void 0 && match(input.code);
		}
		case "query": {
			const { key, pattern } = expr;
			return (input) => {
				const params = new URLSearchParams(input.id.match(QUERY_REGEX)?.[1]);
				if (typeof pattern === "boolean") return params.has(key) === pattern;
				const value = params.get(key);
				if (typeof pattern === "string") return value === pattern;
				const result = pattern.test(value ?? "");
				pattern.lastIndex = 0;
				return result;
			};
		}
		case "moduleType": throw new Error("[unplugin] `moduleType` filter expressions are only supported by Rolldown.");
	}
}
function compileComposableFilter(exprs, hook) {
	const compiled = exprs.map((expr) => ({
		include: expr.kind === "include",
		match: compileExpression(expr.expr, hook)
	}));
	const hasInclude = compiled.some((expr) => expr.include);
	return (input) => {
		for (const { include, match } of compiled) if (match(input)) return include;
		return !hasInclude;
	};
}
function hasCodeExpression(expr) {
	if ("args" in expr) return expr.args.some(hasCodeExpression);
	if ("expr" in expr) return hasCodeExpression(expr.expr);
	return expr.kind === "code";
}
function normalizeObjectHook(name, hook) {
	const matchAll = () => true;
	if (typeof hook === "function") return {
		handler: hook,
		filter: matchAll,
		idFilter: matchAll
	};
	const { handler, filter: hookFilter } = hook;
	if (Array.isArray(hookFilter)) {
		const filter = compileComposableFilter(hookFilter, name);
		if (name !== "transform") return {
			handler,
			filter: (id, importer) => filter({
				id,
				importer
			})
		};
		return {
			handler,
			filter: (id, code) => filter({
				id,
				code
			}),
			idFilter: hookFilter.some(hasCodeExpression) ? matchAll : (id) => filter({ id })
		};
	}
	if (name === "resolveId" || name === "load") return {
		handler,
		filter: createFilterForId(hookFilter?.id) || matchAll
	};
	const transformFilter = hookFilter;
	const idFilter = createIdFilter(transformFilter?.id);
	return {
		handler,
		filter: createFilterForTransform(idFilter, transformFilter?.code) || matchAll,
		idFilter: idFilter || matchAll
	};
}
// src/utils/webpack-like.ts
function createCachedIdFilter(getHook) {
	let cached;
	return (id) => {
		const hook = getHook();
		const cwd = process.cwd();
		if (cached?.hook !== hook || cached.cwd !== cwd) cached = {
			hook,
			cwd,
			filter: normalizeObjectHook("transform", hook).idFilter
		};
		return cached.filter(id);
	};
}
function transformUse(data, plugin, transformLoader, filter) {
	if (data.resource == null) return [];
	const id = normalizeAbsolutePath(data.resource + (data.resourceQuery || ""));
	if (plugin.transformInclude && !plugin.transformInclude(id)) return [];
	if (!filter(id)) return [];
	return [{
		loader: transformLoader,
		options: { plugin },
		ident: plugin.name
	}];
}
/**
* Normalizes a given path when it's absolute. Normalizing means returning a new path by converting
* the input path to the native os format. This is useful in cases where we want to normalize
* the `id` argument of a hook. Any absolute ids should be in the default format
* of the operating system. Any relative imports or node_module imports should remain
* untouched.
*
* @param path - Path to normalize.
* @returns a new normalized path.
*/
function normalizeAbsolutePath(path) {
	if (isAbsolute(path)) return normalize(path);
	else return path;
}
/**
* Webpack escapes special characters in a resource path, such as the `#` of `msg#hash.js`, by
* prefixing them with an escape character. Rspack does the same with a different one.
* Hooks should see the real path, so the escape characters have to be removed again.
*
* Only use this on resource paths, not on virtual module ids: those can legitimately
* start with `\0`, like `\0virtual:foo` in the Rollup convention.
*
* @param path - Resource path of the loader context.
* @param framework - Bundler that produced the path.
* @returns the path without escape characters.
*/
function unescapeResourcePath(path, framework) {
	return framework === "webpack" ? path.replace(/\0([\s\S])/g, "$1") : path.replace(/\u200B([\s\S])/g, "$1");
}
export { normalizeObjectHook as a, setParseImpl as c, unescapeResourcePath as i, normalizeAbsolutePath as n, toArray as o, transformUse as r, parse as s, createCachedIdFilter as t };
