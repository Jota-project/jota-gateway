import { Buffer } from "node:buffer";
import { LoaderContext } from "webpack";
export declare const raw: boolean;
export default function transform(this: LoaderContext<any>, source: string | Buffer, map: any): Promise<void>;