import { Buffer } from "node:buffer";
import { LoaderContext } from "@rspack/core";
export declare const raw: boolean;
export default function load(this: LoaderContext, source: string | Buffer, map: any): Promise<void>;