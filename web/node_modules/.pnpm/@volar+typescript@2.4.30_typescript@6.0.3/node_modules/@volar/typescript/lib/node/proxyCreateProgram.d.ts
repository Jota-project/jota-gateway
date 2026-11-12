import { type Language, type LanguagePlugin } from '@volar/language-core';
import type * as ts from 'typescript';
export declare function proxyCreateProgram(ts: typeof import('typescript'), original: typeof ts['createProgram'], create: (ts: typeof import('typescript'), options: ts.CreateProgramOptions) => LanguagePlugin<string>[] | {
    languagePlugins: LanguagePlugin<string>[];
    setup?(language: Language<string>): void;
}): typeof import("typescript").createProgram;
/**
 * Drops the state kept for projects that the solution builder no longer builds.
 * `isActiveProject` receives the canonical path of a project's config file.
 */
export declare function releaseRemovedProjects(createProgram: object, isActiveProject: (configFilePath: string) => boolean): void;
