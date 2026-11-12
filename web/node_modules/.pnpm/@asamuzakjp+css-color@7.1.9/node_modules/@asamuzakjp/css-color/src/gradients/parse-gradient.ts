/**
 * CSS gradient parsing functionality.
 */

import {
  isFunctionNode,
  parseListOfComponentValues
} from '@csstools/css-parser-algorithms';
import { tokenize } from '@csstools/css-tokenizer';
import { isValidColor } from '../resolvers/resolve-color';
import { ColorStopList, Gradient, GradientType, Options } from '../typedef';
import { createCacheKey, getCache, setCache } from '../utils/cache';
import { isString } from '../utils/common';
import { splitValue } from '../utils/util';
import {
  splitColorStop,
  validateColorStopList,
  validateGradientLine
} from './gradient-util';

/* Constants */
import { GRADIENT, VAL_SPEC } from '../utils/constant';
const NAMESPACE = 'gradient';
const COLOR_OPT = {
  format: VAL_SPEC,
  nullable: true
};

/* Regular expressions */
const IS_GRADIENT = new RegExp(`^${GRADIENT}$`, 'i');

/**
 * Parses a CSS gradient value into a structured gradient object.
 * @param value - The CSS gradient string to parse.
 * @param [opt] - The parsing options.
 * @returns A structured Gradient object, or null if parsing fails.
 */
export const parseGradient = (
  value: string,
  opt: Options = {}
): Gradient | null => {
  if (!isString(value)) {
    return null;
  }
  const trimmedValue = value.trim();
  const cacheKey: string = createCacheKey(
    { namespace: NAMESPACE, name: 'parseGradient', value: trimmedValue },
    opt
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as Gradient | null;
  }
  // Tokenize and parse component values to extract the gradient function node
  const tokens = tokenize({ css: trimmedValue });
  const componentValues = parseListOfComponentValues(tokens);
  if (componentValues.length !== 1 || !isFunctionNode(componentValues[0])) {
    setCache(cacheKey, null);
    return null;
  }
  const fnNode = componentValues[0];
  const type = fnNode.getName().toLowerCase();
  if (!IS_GRADIENT.test(type)) {
    setCache(cacheKey, null);
    return null;
  }
  // Extract the inner value of the gradient function from its node children
  const gradValue = fnNode.value.map(node => node.toString()).join('');
  if (gradValue) {
    const [lineOrColorStop, ...itemList] = splitValue(gradValue, {
      delimiter: ',',
      preserveComment: true
    });
    if (!lineOrColorStop) {
      setCache(cacheKey, null);
      return null;
    }
    const [firstPart] = splitColorStop(lineOrColorStop);
    if (firstPart && isValidColor(splitValue(firstPart).join(' '), COLOR_OPT)) {
      itemList.unshift(lineOrColorStop);
      const { colorStops, valid } = validateColorStopList(itemList, type, opt);
      if (valid) {
        const res: Gradient = {
          value: trimmedValue,
          type: type as GradientType,
          colorStopList: colorStops as ColorStopList
        };
        setCache(cacheKey, res);
        return res;
      }
    } else if (itemList.length > 1) {
      const { line: gradientLine, valid: validLine } = validateGradientLine(
        splitValue(lineOrColorStop).join(' '),
        type
      );
      const { colorStops, valid: validColorStops } = validateColorStopList(
        itemList,
        type,
        opt
      );
      if (validLine && validColorStops) {
        const res: Gradient = {
          value: trimmedValue,
          type: type as GradientType,
          gradientLine,
          colorStopList: colorStops as ColorStopList
        };
        setCache(cacheKey, res);
        return res;
      }
    }
  }
  setCache(cacheKey, null);
  return null;
};
