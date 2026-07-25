// App constants — non-sensitive config that doesn't change per deployment
export const APP_VERSION = '1.0.0';
export const MAX_BULK_COUNT = 50;
export const EXPIRES_HOURS = 24;
export const RATE_LIMIT_WINDOW = 3600; // 1 hour in seconds
export const RATE_LIMIT_MAX = 100;     // max requests per window per IP
