export interface FormatOptions {
  /**
   * A String or Number object that's used to insert white space into the output JSON string for readability purposes.
   *
   * When provided, identation won't be auto detected anymore.
   *
   * When passed at parse time, it is remembered and used when stringifying the value back.
   */
  indent?: string | number;
  /**
   * Set to `false` to skip indentation preservation.
   */
  preserveIndentation?: boolean;
  /**
   * Set to `false` to skip whitespace preservation.
   */
  preserveWhitespace?: boolean;
  /**
   * The number of characters to sample from the start of the text.
   *
   * Default: 1024
   */
  sampleSize?: number;
}