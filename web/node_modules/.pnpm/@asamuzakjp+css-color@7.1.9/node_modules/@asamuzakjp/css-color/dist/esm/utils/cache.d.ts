/**
 * Cache module.
 */
import { LRUCache } from 'lru-cache';
import { Options } from '../typedef';
/**
 * Wrapper class representing a cached item.
 * @template T - The type of the cached item.
 */
export declare class CacheItem<T = unknown> {
    #private;
    /**
     * Creates an instance of CacheItem.
     * @param item - The item to be cached.
     */
    constructor(item: T);
    /**
     * Gets the cached item.
     * @returns The cached item.
     */
    get item(): T;
}
/**
 * LRU cache instance.
 */
export declare const lruCache: LRUCache<string, CacheItem<any>, unknown>;
/**
 * Sets an item in the cache.
 * @template T - The type of the value being cached.
 * @param key - The cache key.
 * @param value - The value or CacheItem instance to cache.
 */
export declare const setCache: <T>(key: string, value: T | CacheItem<T>) => void;
/**
 * Retrieves an item from the cache.
 * @template T - The expected type of the cached value.
 * @param key - The cache key.
 * @returns The cached item, or `false` if the key is empty or not found.
 */
export declare const getCache: <T = unknown>(key: string) => CacheItem<T> | false;
/**
 * Creates a unique cache key based on key data and options.
 * @param keyData - The data containing namespace, name, and value.
 * @param opt - Configuration options affecting the key generation.
 * @returns The generated cache key, or an empty string if invalid or uncacheable.
 */
export declare const createCacheKey: (keyData: Record<string, string>, opt?: Options) => string;
