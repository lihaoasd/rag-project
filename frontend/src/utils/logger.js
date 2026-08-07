/**
 * 前端统一日志工具。
 *
 * 用法：
 *   import { createLogger } from '@/utils/logger'
 *   const logger = createLogger('api')
 *   logger.info('请求成功', { status: 200 })
 *   logger.error('请求失败', error)
 *
 * 生产环境（import.meta.env.PROD）下 debug / info 级别自动静默。
 */

const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 }

/** 当前最低输出级别。生产环境只输出 warn 及以上。 */
const minLevel = import.meta.env.PROD ? LEVELS.warn : LEVELS.debug

/** 格式化时间戳 */
function timestamp() {
  return new Date().toISOString()
}

/** 格式化日志前缀 */
function prefix(level, module) {
  return `[${timestamp()}] [${level}] [${module}]`
}

/**
 * 创建指定模块的 logger。
 * @param {string} module - 模块名（如 'api', 'router', 'store'）
 * @returns {{ debug: Function, info: Function, warn: Function, error: Function }}
 */
export function createLogger(module) {
  return {
    debug(...args) {
      if (minLevel <= LEVELS.debug) {
        console.debug(prefix('DEBUG', module), ...args)
      }
    },
    info(...args) {
      if (minLevel <= LEVELS.info) {
        console.info(prefix('INFO', module), ...args)
      }
    },
    warn(...args) {
      if (minLevel <= LEVELS.warn) {
        console.warn(prefix('WARN', module), ...args)
      }
    },
    error(...args) {
      console.error(prefix('ERROR', module), ...args)
    },
  }
}
