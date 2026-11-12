"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.generateTemplate = generateTemplate;
const codeFeatures_1 = require("../codeFeatures");
const names_1 = require("../names");
const utils_1 = require("../utils");
const merge_1 = require("../utils/merge");
function* generateTemplate(options, selfType) {
    yield* generateTemplateCtx(options, selfType);
    yield* generateTemplateComponents(options);
    yield* generateTemplateDirectives(options);
    yield `void ${names_1.names.ctx}, ${names_1.names.components}, ${names_1.names.intrinsics}, ${names_1.names.directives}${utils_1.endOfLine}`;
    // The template renders after setup, so it runs in its own function: the bindings
    // it reads take their render-time types, not the narrowing at the end of setup.
    // The types setup and the export need from it are passed out through its return type.
    const exportedTypes = [names_1.names.Slots, names_1.names.InheritedAttrs, names_1.names.TemplateRefs, names_1.names.RootEl, names_1.names.StyleModules]
        .filter(name => options.templateAndStyleTypes.has(name) && !(name === names_1.names.Slots && options.scriptSetupRanges?.defineSlots));
    yield `const ${names_1.names.template} = () => {${utils_1.newLine}`;
    for (const name of options.dotValueBindings) {
        yield `${names_1.names.withDotValue}(${name}, ${(0, utils_1.getRefBrandArgument)(options.vueCompilerOptions, options.scriptLang)})${utils_1.endOfLine}`;
    }
    if (options.templateAndStyleCodes.length) {
        yield* options.templateAndStyleCodes;
    }
    yield `return {${utils_1.newLine}`;
    for (const name of exportedTypes) {
        yield `${name}: ${(0, utils_1.asType)(name, options.scriptLang)},${utils_1.newLine}`;
    }
    if (options.templateAndStyleTypes.has(names_1.names.dollars)) {
        yield `${names_1.names.dollars},${utils_1.newLine}`;
    }
    yield `}${utils_1.endOfLine}`;
    yield `}${utils_1.endOfLine}`;
    // Consumed only by the parts of setup and the export that apply.
    const used = [names_1.names.template];
    const returnType = `ReturnType<typeof ${names_1.names.template}>`;
    for (const name of exportedTypes) {
        yield* (0, utils_1.generateTypeAlias)(name, options.scriptLang, function* () {
            yield `${returnType}['${name}']`;
        });
    }
    if (exportedTypes.length) {
        used.push((0, utils_1.asType)(`[${exportedTypes.join(`, `)}]`, options.scriptLang));
    }
    if (options.templateAndStyleTypes.has(names_1.names.dollars)) {
        yield* (0, utils_1.generateTypedVar)('let', names_1.names.dollars, options.scriptLang, function* () {
            yield `${returnType}['${names_1.names.dollars}']`;
        });
        used.push(names_1.names.dollars);
    }
    yield `void [${used.join(`, `)}]${utils_1.endOfLine}`;
}
function* generateTemplateCtx({ vueCompilerOptions, templateAndStyleTypes, scriptSetupRanges, fileName, scriptLang }, selfType) {
    const exps = [];
    const emitTypes = [];
    const propTypes = [];
    if (vueCompilerOptions.petiteVueExtensions.some(ext => fileName.endsWith(ext))) {
        exps.push(`globalThis`);
    }
    if (selfType) {
        exps.push((0, utils_1.asType)(`InstanceType<${names_1.names.PickNotAny}<typeof ${selfType}, new () => {}>>`, scriptLang));
    }
    else {
        exps.push((0, utils_1.asType)(`import('${vueCompilerOptions.lib}').ComponentPublicInstance`, scriptLang));
    }
    if (templateAndStyleTypes.has(names_1.names.StyleModules)) {
        exps.push((0, utils_1.asType)(names_1.names.StyleModules, scriptLang));
    }
    if (scriptSetupRanges?.defineEmits) {
        emitTypes.push(`typeof ${scriptSetupRanges.defineEmits.name ?? names_1.names.emit}`);
    }
    if (scriptSetupRanges?.defineModel.length) {
        emitTypes.push(`typeof ${names_1.names.modelEmit}`);
    }
    if (emitTypes.length) {
        yield* (0, utils_1.generateTypeAlias)(names_1.names.EmitProps, scriptLang, function* () {
            yield `${names_1.names.EmitsToProps}<${names_1.names.NormalizeEmits}<${emitTypes.join(` & `)}>>`;
        });
        exps.push((0, utils_1.asType)(`{ $emit: ${emitTypes.join(` & `)} }`, scriptLang));
    }
    if (scriptSetupRanges?.defineProps) {
        propTypes.push(`typeof ${scriptSetupRanges.defineProps.name ?? names_1.names.props}`);
    }
    if (scriptSetupRanges?.defineModel.length) {
        propTypes.push(names_1.names.ModelProps);
    }
    if (emitTypes.length) {
        propTypes.push(names_1.names.EmitProps);
    }
    if (propTypes.length) {
        exps.push((0, utils_1.asType)(`{ $props: ${propTypes.join(` & `)} }`, scriptLang));
        exps.push((0, utils_1.asType)(propTypes.join(` & `), scriptLang));
    }
    yield `const ${names_1.names.ctx} = `;
    yield* (0, merge_1.generateSpreadMerge)(...exps);
    yield utils_1.endOfLine;
}
function* generateTemplateComponents({ vueCompilerOptions, script, scriptRanges, localComponents, scriptLang }) {
    const types = [];
    if (localComponents.size) {
        types.push(generateExposedType(vueCompilerOptions.lib, localComponents));
    }
    if (script && scriptRanges?.exportDefault?.options?.components) {
        const { components } = scriptRanges.exportDefault.options;
        yield `const ${names_1.names.componentsOption} = `;
        yield* (0, utils_1.generateSfcBlockSection)(script, components.start, components.end, codeFeatures_1.codeFeatures.navigation);
        yield utils_1.endOfLine;
        types.push(`typeof ${names_1.names.componentsOption}`);
    }
    yield* (0, utils_1.generateTypeAlias)(names_1.names.LocalComponents, scriptLang, function* () {
        yield types.length ? types.join(` & `) : `{}`;
    });
    yield* (0, utils_1.generateTypeAlias)(names_1.names.GlobalComponents, scriptLang, function* () {
        yield vueCompilerOptions.target >= 3.5
            ? `import('${vueCompilerOptions.lib}').GlobalComponents`
            : `import('${vueCompilerOptions.lib}').GlobalComponents & Pick<typeof import('${vueCompilerOptions.lib}'), 'Transition' | 'TransitionGroup' | 'KeepAlive' | 'Suspense' | 'Teleport'>`;
    });
    yield* (0, utils_1.generateTypedVar)('let', names_1.names.components, scriptLang, function* () {
        yield `${names_1.names.LocalComponents} & ${names_1.names.GlobalComponents}`;
    });
    yield* (0, utils_1.generateTypedVar)('let', names_1.names.intrinsics, scriptLang, function* () {
        yield vueCompilerOptions.target >= 3.3
            ? `import('${vueCompilerOptions.lib}/jsx-runtime').JSX.IntrinsicElements`
            : `globalThis.JSX.IntrinsicElements`;
    });
}
function* generateTemplateDirectives({ vueCompilerOptions, script, scriptRanges, localDirectives, scriptLang }) {
    const types = [];
    if (localDirectives.size) {
        types.push(generateExposedType(vueCompilerOptions.lib, localDirectives));
    }
    if (script && scriptRanges?.exportDefault?.options?.directives) {
        const { directives } = scriptRanges.exportDefault.options;
        yield `const ${names_1.names.directivesOption} = `;
        yield* (0, utils_1.generateSfcBlockSection)(script, directives.start, directives.end, codeFeatures_1.codeFeatures.navigation);
        yield utils_1.endOfLine;
        types.push(`${names_1.names.ResolveDirectives}<typeof ${names_1.names.directivesOption}>`);
    }
    yield* (0, utils_1.generateTypeAlias)(names_1.names.LocalDirectives, scriptLang, function* () {
        yield types.length ? types.join(` & `) : `{}`;
    });
    yield* (0, utils_1.generateTypedVar)('let', names_1.names.directives, scriptLang, function* () {
        yield `${names_1.names.LocalDirectives} & import('${vueCompilerOptions.lib}').GlobalDirectives`;
    });
}
function generateExposedType(lib, bindings) {
    return `import('${lib}').ShallowUnwrapRef<{\n${[...bindings].map(name => `${name}: typeof ${name};`).join(`\n`)}\n}>`;
}
//# sourceMappingURL=template.js.map