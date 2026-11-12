"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.proxyCreateProgram = proxyCreateProgram;
exports.releaseRemovedProjects = releaseRemovedProjects;
const language_core_1 = require("@volar/language-core");
const common_1 = require("../common");
const resolveModuleName_1 = require("../resolveModuleName");
const decorateProgram_1 = require("./decorateProgram");
const utils_1 = require("./utils");
const arrayEqual = (a, b) => {
    if (a.length !== b.length) {
        return false;
    }
    for (let i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) {
            return false;
        }
    }
    return true;
};
const objectEqual = (a, b) => {
    const keysA = Object.keys(a);
    const keysB = Object.keys(b);
    if (keysA.length !== keysB.length) {
        return false;
    }
    for (const key of keysA) {
        if (a[key] !== b[key]) {
            return false;
        }
    }
    return true;
};
const projectStateReleasers = new WeakMap();
function proxyCreateProgram(ts, original, create) {
    const sourceFileSnapshots = new language_core_1.FileMap(ts.sys.useCaseSensitiveFileNames);
    const parsedSourceFiles = new WeakMap();
    // Keyed by the canonical `configFilePath`, so the projects of a solution builder
    // (`tsc -b`) don't evict each other's state; programs without one share a slot.
    const states = new Map();
    // Bumped whenever a program's root files or options change. A kept module resolution
    // cache may hold lookups that a file added or removed since then invalidates, so the
    // other projects recreate theirs before they are built again.
    let fileSetVersion = 0;
    const proxy = new Proxy(original, {
        apply: (target, thisArg, args) => {
            const options = args[0];
            assert(!!options.host, '!!options.host');
            const configFilePath = options.options.configFilePath;
            const stateKey = typeof configFilePath === 'string'
                ? options.host.getCanonicalFileName(configFilePath)
                : undefined;
            let state = states.get(stateKey);
            if (!state
                || !arrayEqual(options.rootNames, state.options.rootNames)
                || !objectEqual(options.options, state.options.options)) {
                if (state) {
                    fileSetVersion++;
                }
                const moduleResolutionCache = createModuleResolutionCache(options.host, options.options);
                const created = create(ts, options);
                const languagePlugins = Array.isArray(created) ? created : created.languagePlugins;
                const language = (0, language_core_1.createLanguage)([
                    ...languagePlugins,
                    { getLanguageId: common_1.resolveFileLanguageId },
                ], new language_core_1.FileMap(ts.sys.useCaseSensitiveFileNames), (fileName, includeFsFiles) => {
                    if (includeFsFiles && !sourceFileSnapshots.has(fileName)) {
                        const sourceFileText = originalHost.readFile(fileName);
                        if (sourceFileText !== undefined) {
                            sourceFileSnapshots.set(fileName, [undefined, {
                                    getChangeRange() {
                                        return undefined;
                                    },
                                    getLength() {
                                        return sourceFileText.length;
                                    },
                                    getText(start, end) {
                                        return sourceFileText.substring(start, end);
                                    },
                                }]);
                        }
                        else {
                            sourceFileSnapshots.set(fileName, [undefined, undefined]);
                        }
                    }
                    const snapshot = sourceFileSnapshots.get(fileName)?.[1];
                    if (snapshot) {
                        language.scripts.set(fileName, snapshot);
                    }
                    else {
                        language.scripts.delete(fileName);
                    }
                });
                if ('setup' in created) {
                    created.setup?.(language);
                }
                state = { options, languagePlugins, language, moduleResolutionCache, fileSetVersion };
                states.set(stateKey, state);
            }
            else if (state.fileSetVersion !== fileSetVersion) {
                state.moduleResolutionCache = createModuleResolutionCache(options.host, options.options);
                state.fileSetVersion = fileSetVersion;
            }
            const { languagePlugins, language, moduleResolutionCache } = state;
            const originalHost = options.host;
            const pluginExtensions = languagePlugins
                .map(plugin => plugin.typescript?.extraFileExtensions.map(({ extension }) => `.${extension}`) ?? [])
                .flat();
            // TypeScript prefers `getSourceFileByPath` over `getSourceFile` when reusing an old
            // program (e.g. `tsc --watch`), which would bypass the wrapper below.
            options.host = { ...originalHost, getSourceFileByPath: undefined };
            options.host.getSourceFile = (fileName, languageVersionOrOptions, onError, shouldCreateNewSourceFile) => {
                const originalSourceFile = originalHost.getSourceFile(fileName, languageVersionOrOptions, onError, shouldCreateNewSourceFile);
                if (!sourceFileSnapshots.has(fileName)
                    || sourceFileSnapshots.get(fileName)?.[0] !== originalSourceFile) {
                    if (originalSourceFile) {
                        sourceFileSnapshots.set(fileName, [originalSourceFile, {
                                getChangeRange() {
                                    return undefined;
                                },
                                getLength() {
                                    return originalSourceFile.text.length;
                                },
                                getText(start, end) {
                                    return originalSourceFile.text.substring(start, end);
                                },
                            }]);
                    }
                    else {
                        sourceFileSnapshots.set(fileName, [undefined, undefined]);
                    }
                }
                if (!originalSourceFile) {
                    return;
                }
                if (!parsedSourceFiles.has(originalSourceFile)) {
                    const sourceScript = language.scripts.get(fileName);
                    assert(!!sourceScript, '!!sourceScript');
                    parsedSourceFiles.set(originalSourceFile, undefined);
                    if (sourceScript.generated?.languagePlugin.typescript) {
                        const { getServiceScript, getExtraServiceScripts } = sourceScript.generated.languagePlugin.typescript;
                        const serviceScript = getServiceScript(sourceScript.generated.root);
                        if (serviceScript) {
                            let virtualContents;
                            if (!serviceScript.preventLeadingOffset) {
                                virtualContents = originalSourceFile.text.split('\n').map(line => ' '.repeat(line.length)).join('\n')
                                    + serviceScript.code.snapshot.getText(0, serviceScript.code.snapshot.getLength());
                            }
                            else {
                                virtualContents = serviceScript.code.snapshot.getText(0, serviceScript.code.snapshot.getLength());
                            }
                            const parsedSourceFile = ts.createSourceFile(fileName, virtualContents, languageVersionOrOptions, undefined, serviceScript.scriptKind);
                            // @ts-expect-error
                            parsedSourceFile.version = originalSourceFile.version;
                            // @ts-expect-error
                            originalSourceFile.scriptKind = serviceScript.scriptKind;
                            parsedSourceFiles.set(originalSourceFile, parsedSourceFile);
                        }
                        if (getExtraServiceScripts) {
                            console.warn('getExtraServiceScripts() is not available in this use case.');
                        }
                    }
                }
                return parsedSourceFiles.get(originalSourceFile) ?? originalSourceFile;
            };
            if (pluginExtensions.length) {
                options.options.allowArbitraryExtensions = true;
                const resolveModuleName = (0, resolveModuleName_1.createResolveModuleName)(ts, ts.sys.getFileSize, originalHost, language.plugins, fileName => language.scripts.get(fileName));
                const resolveModuleNameLiterals = originalHost.resolveModuleNameLiterals;
                const resolveModuleNames = originalHost.resolveModuleNames;
                // When any plugin opts in to `resolveHiddenExtensions`, the volar-aware
                // resolver above is what makes extensionless imports of plugin-extension
                // files resolvable (it answers `fileExists('Foo.d.ts')` as `true` when
                // `Foo.<ext>` is on disk). If the host already has a `resolveModuleNameLiterals`
                // — e.g. `tsc --watch` installs `resolutionCache.resolveModuleNameLiterals` —
                // the original fast-path delegates extensionless imports to it, bypassing
                // the wrapper and surfacing TS2307. Force the volar-aware resolver in
                // that case.
                const hasResolveHiddenExtensions = languagePlugins.some(plugin => plugin.typescript?.resolveHiddenExtensions);
                options.host.resolveModuleNameLiterals = (moduleLiterals, containingFile, redirectedReference, compilerOptions, containingSourceFile, ...rest) => {
                    const disposeFixup = (0, utils_1.fixupImpliedNodeFormatForFile)(ts, pluginExtensions, containingSourceFile, moduleResolutionCache.getPackageJsonInfoCache(), originalHost, compilerOptions);
                    try {
                        if (resolveModuleNameLiterals
                            && !hasResolveHiddenExtensions
                            && moduleLiterals.every(name => !pluginExtensions.some(ext => name.text.endsWith(ext)))) {
                            return resolveModuleNameLiterals(moduleLiterals, containingFile, redirectedReference, compilerOptions, containingSourceFile, ...rest);
                        }
                        return moduleLiterals.map(moduleLiteral => {
                            const mode = ts.getModeForUsageLocation(containingSourceFile, moduleLiteral, compilerOptions);
                            return resolveModuleName(moduleLiteral.text, containingFile, compilerOptions, moduleResolutionCache, redirectedReference, mode);
                        });
                    }
                    finally {
                        disposeFixup?.();
                    }
                };
                options.host.resolveModuleNames = (moduleNames, containingFile, reusedNames, redirectedReference, compilerOptions, containingSourceFile) => {
                    if (resolveModuleNames
                        && !hasResolveHiddenExtensions
                        && moduleNames.every(name => !pluginExtensions.some(ext => name.endsWith(ext)))) {
                        return resolveModuleNames(moduleNames, containingFile, reusedNames, redirectedReference, compilerOptions, containingSourceFile);
                    }
                    return moduleNames.map(moduleName => {
                        return resolveModuleName(moduleName, containingFile, compilerOptions, moduleResolutionCache, redirectedReference, containingSourceFile?.impliedNodeFormat).resolvedModule;
                    });
                };
            }
            const program = Reflect.apply(target, thisArg, args);
            (0, decorateProgram_1.decorateProgram)(language, program);
            return program;
        },
    });
    function createModuleResolutionCache(host, options) {
        return ts.createModuleResolutionCache(host.getCurrentDirectory(), host.getCanonicalFileName, options);
    }
    projectStateReleasers.set(proxy, isActiveProject => {
        for (const key of states.keys()) {
            if (key !== undefined && !isActiveProject(key)) {
                states.delete(key);
            }
        }
    });
    return proxy;
}
/**
 * Drops the state kept for projects that the solution builder no longer builds.
 * `isActiveProject` receives the canonical path of a project's config file.
 */
function releaseRemovedProjects(createProgram, isActiveProject) {
    projectStateReleasers.get(createProgram)?.(isActiveProject);
}
function assert(condition, message) {
    if (!condition) {
        console.error(message);
        throw new Error(message);
    }
}
//# sourceMappingURL=proxyCreateProgram.js.map