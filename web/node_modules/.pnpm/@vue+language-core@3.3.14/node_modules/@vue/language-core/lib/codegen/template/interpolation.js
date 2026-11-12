"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.generateInterpolation = generateInterpolation;
exports.generateIdentifier = generateIdentifier;
const shared_1 = require("../../utils/shared");
const codeFeatures_1 = require("../codeFeatures");
const names_1 = require("../names");
const utils_1 = require("../utils");
const boundary_1 = require("../utils/boundary");
const bindingReferences_1 = require("./bindingReferences");
function* generateInterpolation(options, ctx, block, features, code, start, prefix = '', suffix = '', inNarrowing = false) {
    if (prefix) {
        yield prefix;
    }
    // Assertion narrowing does not flow into functions for imports and `let`/`var`
    // bindings (TS limitation), so each function body re-asserts the ones it reads.
    // Parameter defaults run before the body, so each one that reads such a binding
    // re-asserts it in an immediately invoked arrow, whose flow stays in the function.
    // Bodies are buffered until their end, when those reads are known.
    const bodies = options.reassertBindings?.size && !utils_1.identifierRE.test(code)
        ? collectFunctionBodies(options.typescript, (0, utils_1.getTypeScriptAST)(options.typescript, block, prefix + code + suffix))
            .map(body => ({ ...body, start: body.start - prefix.length, end: body.end - prefix.length }))
        : [];
    // Exits precede enters at the same offset; the stable sort keeps the inner exit and the outer enter first.
    const marks = [
        ...bodies.map(body => ({ offset: body.end, body, enter: false })).reverse(),
        ...bodies.map(body => ({ offset: body.start, body, enter: true })),
    ].sort((a, b) => a.offset - b.offset);
    const frames = [];
    let markIndex = 0;
    function* write(codes) {
        if (frames.length) {
            frames[frames.length - 1].codes.push(...codes);
        }
        else {
            yield* codes;
        }
    }
    function* generateCodeUntil(from, end) {
        while (markIndex < marks.length && marks[markIndex].offset <= end) {
            const { offset, body, enter } = marks[markIndex++];
            yield* write(generateNonIdentifierCode(code.slice(from, offset), block.name, start + from, features, from > 0));
            from = offset;
            if (enter) {
                frames.push({ body, codes: [], reasserts: new Set() });
            }
            else {
                yield* write(generateFunctionBody(frames.pop()));
            }
        }
        yield* write(generateNonIdentifierCode(code.slice(from, end), block.name, start + from, features, from > 0));
    }
    function* generateFunctionBody({ body, codes, reasserts }) {
        if (!reasserts.size) {
            yield* codes;
            return;
        }
        if (body.kind === 'block') {
            yield utils_1.newLine;
            yield* generateReasserts(reasserts);
            yield* codes;
            return;
        }
        // Diagnostics reported on the generated call, or on the `return` keyword (where TS reports
        // return type mismatches), map back to the original expression.
        const verification = { verification: features.verification };
        if (body.kind === 'parameter') {
            const boundary = yield* boundary_1.Boundary.start(block.name, start + body.start, start + body.end, verification);
            yield `(() => {${utils_1.newLine}`;
            yield* generateReasserts(reasserts);
            yield `return (`;
            yield* codes;
            yield `)${utils_1.endOfLine}})()`;
            yield boundary.end();
        }
        else {
            yield `{${utils_1.newLine}`;
            yield* generateReasserts(reasserts);
            const boundary = yield* boundary_1.Boundary.start(block.name, start + body.start, start + body.end, verification);
            yield `return`;
            yield boundary.end();
            yield ` (`;
            yield* codes;
            yield `)${utils_1.endOfLine}}`;
        }
    }
    function* generateReasserts(reasserts) {
        for (const name of reasserts) {
            yield `${names_1.names.withDotValue}(${name}, ${(0, utils_1.getRefBrandArgument)(options.vueCompilerOptions, options.scriptLang)})${utils_1.endOfLine}`;
        }
    }
    let prevEnd = 0;
    for (const [name, offset, isShorthand, isNarrowing, inTypeQuery, isNewOperand] of forEachIdentifiers(options.typescript, ctx, block, code, prefix, suffix, inNarrowing)) {
        if (isShorthand) {
            yield* generateCodeUntil(prevEnd, offset + name.length);
            yield* write([`: `]);
        }
        else {
            yield* generateCodeUntil(prevEnd, offset);
        }
        if (frames.length
            && options.reassertBindings.has(name)
            && !options.setupConsts.has(name)
            && !options.setupRefs.has(name)
            && !(0, bindingReferences_1.shouldIdentifierSkipped)(ctx, name)) {
            frames[frames.length - 1].reasserts.add(name);
        }
        const codes = [[
                name,
                block.name,
                start + offset,
                isShorthand
                    ? { ...features, __shorthandExpression: 'js' }
                    : features,
            ]];
        yield* write(generateIdentifier(options, ctx, codes, name, block.name, start + offset, start + offset + name.length, isNarrowing, inTypeQuery, isNewOperand));
        prevEnd = offset + name.length;
    }
    yield* generateCodeUntil(prevEnd, code.length);
    if (suffix) {
        yield suffix;
    }
}
// Function scopes in pre-order: the expressions a parameter list evaluates, then the body.
// A block body spans from after its directive prologue to its closing brace.
function collectFunctionBodies(ts, ast) {
    const bodies = [];
    const visitBinding = (name) => {
        if (ts.isIdentifier(name)) {
            return;
        }
        for (const element of name.elements) {
            if (ts.isOmittedExpression(element)) {
                continue;
            }
            if (element.propertyName && ts.isComputedPropertyName(element.propertyName)) {
                pushParameterExpression(element.propertyName.expression);
            }
            visitBinding(element.name);
            if (element.initializer) {
                pushParameterExpression(element.initializer);
            }
        }
    };
    const pushParameterExpression = (node) => {
        bodies.push({ ...(0, shared_1.getStartEnd)(ts, node, ast), kind: 'parameter' });
        visit(node);
    };
    const visit = (node) => {
        if ((ts.isArrowFunction(node)
            || ts.isFunctionExpression(node)
            || ts.isFunctionDeclaration(node)
            || ts.isMethodDeclaration(node)
            || ts.isGetAccessorDeclaration(node)
            || ts.isSetAccessorDeclaration(node)
            || ts.isConstructorDeclaration(node)
            || ts.isClassStaticBlockDeclaration(node))
            && node.body) {
            if (!ts.isClassStaticBlockDeclaration(node)) {
                for (const modifier of node.modifiers ?? []) {
                    visit(modifier);
                }
                if (node.name) {
                    visit(node.name);
                }
                for (const typeParameter of node.typeParameters ?? []) {
                    visit(typeParameter);
                }
                for (const parameter of node.parameters) {
                    for (const modifier of parameter.modifiers ?? []) {
                        visit(modifier);
                    }
                    visitBinding(parameter.name);
                    if (parameter.type) {
                        visit(parameter.type);
                    }
                    if (parameter.initializer) {
                        pushParameterExpression(parameter.initializer);
                    }
                }
                if (node.type) {
                    visit(node.type);
                }
            }
            const { start, end } = (0, shared_1.getStartEnd)(ts, node.body, ast);
            if (ts.isBlock(node.body)) {
                const statements = node.body.statements;
                const prologue = statements.findIndex(statement => !ts.isExpressionStatement(statement) || !ts.isStringLiteral(statement.expression));
                if (prologue !== -1) {
                    bodies.push({
                        start: prologue ? statements[prologue - 1].end : start + 1,
                        end: end - 1,
                        kind: 'block',
                    });
                }
            }
            else {
                bodies.push({ start, end, kind: 'expression' });
            }
            visit(node.body);
            return;
        }
        ts.forEachChild(node, visit);
    };
    ts.forEachChild(ast, visit);
    return bodies;
}
/**
 * Yield a code chunk, cutting the boundary character off as verification-only.
 *
 * Adjacent mappings share the boundary offset (closed interval), so both the
 * neighbouring token's end and this chunk's start claim the same source offset.
 * Downgrading the boundary character to verification-only keeps content-sensitive
 * features (rename / navigation) from firing on the neighbouring chunk.
 */
function* generateNonIdentifierCode(code, source, offset, data, shouldCut = true) {
    if (!code.length) {
        return;
    }
    if (!shouldCut) {
        yield [code, source, offset, data];
        return;
    }
    yield [code.slice(0, 1), source, offset, { verification: data.verification }];
    if (code.length > 1) {
        yield [code.slice(1), source, offset + 1, data];
    }
}
// Access strategy, in precedence order:
// - setup consts → direct reference
// - local-scope / global names → direct reference (the interpolation path
//   filters these earlier; the v-bind shorthand path relies on this branch)
// - template refs → direct `.value`
// - dotValue bindings (narrowed at least once anywhere) → `.value` at
//   every position; narrowing then works on the `.value` reference chain
// - other bindings → `__VLS_unwrap` (plain reads keep the original type)
// - otherwise → `__VLS_ctx.<name>`
function* generateIdentifier(options, ctx, codes, name, source, start, end, isNarrowing = false, inTypeQuery = false, isNewOperand = false) {
    if (options.setupConsts.has(name) || (0, bindingReferences_1.shouldIdentifierSkipped)(ctx, name)) {
        yield* codes;
    }
    else if (options.setupRefs.has(name)) {
        yield* codes;
        yield `.`;
        const boundary = yield* boundary_1.Boundary.start(source, start, end, codeFeatures_1.codeFeatures.verification);
        yield `value`;
        yield boundary.end();
    }
    else if (options.setupBindings.has(name)) {
        // First pass records narrowing accesses here; the second pass emits from dotValueBindings.
        ctx.accessVariable(source, name, start, inTypeQuery || isNarrowing);
        if (inTypeQuery || options.dotValueBindings.has(name)) {
            yield* codes;
            yield `.`;
            const boundary = yield* boundary_1.Boundary.start(source, start, end, codeFeatures_1.codeFeatures.verification);
            yield `value`;
            yield boundary.end();
        }
        else {
            // `new __VLS_unwrap(Foo)()` parses as `new (__VLS_unwrap(Foo)())`,
            // whose target lacks a construct signature; keep the operand parenthesized.
            if (isNewOperand) {
                yield `(`;
            }
            yield `${names_1.names.unwrap}(`;
            yield* codes;
            yield `, ${(0, utils_1.getRefBrandArgument)(options.vueCompilerOptions, options.scriptLang)})`;
            if (isNewOperand) {
                yield `)`;
            }
        }
    }
    else {
        // #1205, #1264
        const boundary = yield* boundary_1.Boundary.start(source, start, end, codeFeatures_1.codeFeatures.verification);
        if (ctx.dollarVars.has(name)) {
            yield names_1.names.dollars;
        }
        else {
            ctx.accessVariable(source, name, start);
            yield names_1.names.ctx;
        }
        yield `.`;
        yield* codes;
        yield boundary.end();
    }
}
function* forEachIdentifiers(ts, ctx, block, code, prefix, suffix, inNarrowing) {
    if (utils_1.identifierRE.test(code) && !(0, bindingReferences_1.shouldIdentifierSkipped)(ctx, code)) {
        yield [code, 0, false, inNarrowing, false, false];
        return;
    }
    const scope = ctx.scope();
    const ast = (0, utils_1.getTypeScriptAST)(ts, block, prefix + code + suffix);
    for (const { id, isShorthand, isNarrowing, skipped, inTypeQuery, isNewOperand } of (0, bindingReferences_1.forEachDeclarations)(ts, ast, ast, ctx, scope, inNarrowing)) {
        if (skipped) {
            continue;
        }
        const text = (0, shared_1.getNodeText)(ts, id, ast);
        yield [text, (0, shared_1.getStartEnd)(ts, id, ast).start - prefix.length, isShorthand, isNarrowing, inTypeQuery, isNewOperand];
    }
    scope.end();
}
//# sourceMappingURL=interpolation.js.map