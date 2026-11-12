/**
 * Cache module.
 */

import { LRUCache } from 'lru-cache';
import { Options } from '../typedef';

/* Constants */
const CACHE_SIZE = 4096;

/**
 * Wrapper class representing a cached item.
 * @template T - The type of the cached item.
 */
export class CacheItem<T = unknown> {
  #item: T;

  /**
   * Creates an instance of CacheItem.
   * @param item - The item to be cached.
   */
  constructor(item: T) {
    this.#item = item;
  }

  /**
   * Gets the cached item.
   * @returns The cached item.
   */
  get item(): T {
    return this.#item;
  }
}

/**
 * LRU cache instance.
 */
export const lruCache = new LRUCache<string, CacheItem<any>>({
  max: CACHE_SIZE
});

/**
 * Sets an item in the cache.
 * @template T - The type of the value being cached.
 * @param key - The cache key.
 * @param value - The value or CacheItem instance to cache.
 */
export const setCache = <T>(key: string, value: T | CacheItem<T>): void => {
  if (!key) {
    return;
  }
  if (value instanceof CacheItem) {
    lruCache.set(key, value);
  } else {
    lruCache.set(key, new CacheItem<T>(value));
  }
};

/**
 * Retrieves an item from the cache.
 * @template T - The expected type of the cached value.
 * @param key - The cache key.
 * @returns The cached item, or `false` if the key is empty or not found.
 */
export const getCache = <T = unknown>(key: string): CacheItem<T> | false => {
  if (!key) {
    return false;
  }
  const item = lruCache.get(key);
  if (item !== undefined) {
    return item as CacheItem<T>;
  }
  return false;
};

/**
 * Helper function to stringify an object with its keys sorted alphabetically.
 * @param obj - The object to stringify.
 * @returns A formatted string representation of the object's sorted keys and values.
 */
const stringifySorted = (obj: Record<string, unknown>): string => {
  const keys = Object.keys(obj);
  if (keys.length === 0) {
    return '';
  }
  keys.sort();
  let result = '';
  for (const key of keys) {
    result += `${key}:${JSON.stringify(obj[key])};`;
  }
  return result;
};

/**
 * Creates a unique cache key based on key data and options.
 * @param keyData - The data containing namespace, name, and value.
 * @param opt - Configuration options affecting the key generation.
 * @returns The generated cache key, or an empty string if invalid or uncacheable.
 */
export const createCacheKey = (
  keyData: Record<string, string>,
  opt: Options = {}
): string => {
  if (
    !keyData ||
    (opt.customProperty && typeof opt.customProperty.callback === 'function') ||
    (opt.dimension && typeof opt.dimension.callback === 'function')
  ) {
    return '';
  }
  const namespace = keyData.namespace || '';
  const name = keyData.name || '';
  const value = keyData.value || '';
  if (!namespace && !name && !value) {
    return '';
  }
  const baseKey = `${namespace}:${name}:${value}`;
  const opts = [];
  for (const [key, val] of Object.entries(opt)) {
    if (key !== 'customProperty' && key !== 'dimension') {
      opts.push(`${key}_${val}`);
    }
  }
  const customPropStr = opt.customProperty
    ? stringifySorted(opt.customProperty as Record<string, unknown>)
    : '';
  const dimStr = opt.dimension
    ? stringifySorted(opt.dimension as Record<string, unknown>)
    : '';
  return `${baseKey}:${opts.sort().join('|')}:${customPropStr}:${dimStr}`;
};
