import remapping from "@jridgewell/remapping";
// src/utils/sourcemap.ts
function parseMap(map) {
	return typeof map === "string" ? JSON.parse(map) : map;
}
/**
* Pick the source map a loader should pass on after a transform.
* The first loader in a chain receives no input map, so the transformed map is used as is;
* otherwise both layers are composed instead of dropping either one.
*/
function mergeSourceMaps(transformed, input, resource) {
	if (!transformed) return input;
	const inputMap = parseMap(input);
	if (!inputMap || inputMap.mappings === "") return transformed;
	const transformedMap = parseMap(transformed);
	if (transformedMap.mappings === "" || !transformedMap.sources) return input;
	if (transformedMap.sources.length === 1) return remapping([transformedMap, inputMap], () => null);
	const file = resource?.split("?")[0].replace(/\\/g, "/");
	let traced = false;
	return remapping(transformedMap, (source) => {
		if (traced) return null;
		const normalized = source.replace(/\\/g, "/").replace(/^\.\//, "");
		if (!file || file !== normalized && !file.endsWith(`/${normalized}`)) return null;
		traced = true;
		return inputMap;
	});
}
export { mergeSourceMaps as t };
