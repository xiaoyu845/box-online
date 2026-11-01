'use strict'

const crypto = require('crypto')
const fs = require('fs')
const os = require('os')
const path = require('path')
const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const mods = Array.isArray(cfg.mods) && cfg.mods.length
  ? cfg.mods
  : [
      ['minecraft', '1.12.2'],
      ['mcp', '9.42'],
      ['FML', '8.0.99.99'],
      ['forge', '14.23.5.2860'],
      ['adventureworld', '5.0.0'],
      ['bpl', '1.0.1'],
      ['email', '1.0.1'],
      ['Furniture', '1.3.0'],
      ['shopblock', '1.1.0']
    ]

function emit (obj) {
  process.stdout.write('MF ' + JSON.stringify(obj) + '\n')
}

function varint (n) {
  const out = []
  let v = n >>> 0
  while (true) {
    let b = v & 0x7f
    v >>>= 7
    if (v) out.push(b | 0x80)
    else { out.push(b); break }
  }
  return Buffer.from(out)
}

function mcString (s) {
  const raw = Buffer.from(String(s), 'utf8')
  return Buffer.concat([varint(raw.length), raw])
}

function plugin (channel, data) {
  bot._client.write('custom_payload', { channel, data })
}

function readVarint (buf, offset) {
  let num = 0
  let shift = 0
  let i = offset
  while (i < buf.length && shift < 35) {
    const b = buf[i++]
    num |= (b & 0x7f) << shift
    if ((b & 0x80) === 0) return { value: num >>> 0, offset: i }
    shift += 7
  }
  return null
}

function readMcString (buf, offset) {
  const len = readVarint(buf, offset)
  if (!len) return null
  const end = len.offset + len.value
  if (end > buf.length) return null
  return { text: buf.slice(len.offset, end).toString('utf8'), offset: end }
}

const FILE_LIMIT = 0x6400000
let fileMd5Cat = ''
let fileMd5Count = 0
let fileMd5Err = ''

function javaMac (mac) {
  const parts = String(mac || '').split(/[:\-]/).filter(Boolean)
  if (!parts.length) return ''
  return parts.map((hex, i) => {
    let v = parseInt(hex, 16)
    if (Number.isNaN(v)) v = 0
    if (v > 127) v -= 256
    const text = v < 0 ? (v >>> 0).toString(16).toUpperCase() : v.toString(16).toUpperCase().padStart(2, '0')
    return text + (i < parts.length - 1 ? '-' : '')
  }).join('')
}

function machineId () {
  const nics = os.networkInterfaces()
  let fallback = ''
  for (const name of Object.keys(nics)) {
    for (const nic of nics[name] || []) {
      if (nic.internal || nic.family !== 'IPv4' || !nic.mac || nic.mac === '00:00:00:00:00:00') continue
      const formatted = javaMac(nic.mac)
      const ip = nic.address || ''
      const site = ip.startsWith('10.') || ip.startsWith('192.168.') || /^172\.(1[6-9]|2\d|3[0-1])\./.test(ip)
      if (site) return formatted
      if (!fallback) fallback = formatted
    }
  }
  return fallback
}

function pushJar (list, filePath, gameDir) {
  let st
  try { st = fs.statSync(filePath) } catch (e) { return }
  if (!st.isFile()) return
  const full = path.resolve(filePath)
  const root = path.resolve(gameDir)
  if (!full.toLowerCase().startsWith(root.toLowerCase())) return
  if (st.size > FILE_LIMIT && !path.basename(full).startsWith('ignore_')) {
    fileMd5Err = 'Unsupported file: ' + full
    return
  }
  list.push(full)
}

function buildFileMd5 (gameDir) {
  const list = []
  const versionId = '1.12.2-Forge_14.23.5.2860'
  const versionJson = path.join(gameDir, 'versions', versionId, versionId + '.json')
  if (fs.existsSync(versionJson)) {
    const parsed = JSON.parse(fs.readFileSync(versionJson, 'utf8'))
    for (const lib of parsed.libraries || []) {
      const rel = lib.downloads && lib.downloads.artifact && lib.downloads.artifact.path
      if (!rel) continue
      pushJar(list, path.join(gameDir, 'libraries', rel), gameDir)
    }
  }
  pushJar(list, path.join(gameDir, 'versions', versionId, versionId + '.jar'), gameDir)
  const modsDir = path.join(gameDir, 'mods')
  if (fs.existsSync(modsDir)) {
    const names = fs.readdirSync(modsDir).filter((n) => n.toLowerCase().endsWith('.jar')).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))
    for (const name of names) pushJar(list, path.join(modsDir, name), gameDir)
  }
  if (fileMd5Err) return
  const chunks = []
  for (const file of list) {
    chunks.push(crypto.createHash('md5').update(fs.readFileSync(file)).digest('hex'))
  }
  fileMd5Cat = chunks.join('')
  fileMd5Count = list.length
}

function gameDir () {
  if (cfg.gameDir && fs.existsSync(cfg.gameDir)) return cfg.gameDir
  const hang = process.env.BOX_ONLINE_HANG_ROOT
  if (hang) {
    const d = path.join(hang, '.minecraft')
    if (fs.existsSync(d)) return d
  }
  const desktop = path.join(os.homedir(), 'Desktop')
  try {
    for (const name of fs.readdirSync(desktop)) {
      const d = path.join(desktop, name, '.minecraft')
      if (fs.existsSync(path.join(desktop, name, 'mf_bot')) && fs.existsSync(d)) return d
    }
  } catch (e) { /* 桌面不可读就退回下面 */ }
  return path.join(desktop, '.minecraft')
}

function encryptFiles (secretKeyB64) {
  const key = Buffer.from(String(secretKeyB64), 'base64')
  const algo = key.length === 16 ? 'aes-128-ecb' : key.length === 24 ? 'aes-192-ecb' : key.length === 32 ? 'aes-256-ecb' : ''
  if (!algo) throw new Error('secretKey length ' + key.length)
  const cipher = crypto.createCipheriv(algo, key, null)
  const enc = Buffer.concat([cipher.update(Buffer.from(fileMd5Cat, 'utf8')), cipher.final()])
  return enc.toString('base64')
}

function replyInfo (data) {
  const parsed = readMcString(data, 1)
  if (!parsed) return
  let req
  try { req = JSON.parse(parsed.text) } catch (e) { return }
  const body = {}
  if (Object.prototype.hasOwnProperty.call(req, 'machine')) {
    const machine = machineId()
    if (machine) body.machine = machine
  }
  if (Object.prototype.hasOwnProperty.call(req, 'mods')) {
    body.mods = mods.map((pair) => {
      const mid = Array.isArray(pair) ? pair[0] : pair.modid
      const ver = Array.isArray(pair) ? pair[1] : pair.version
      return mid + ':' + ver
    })
  }
  if (Object.prototype.hasOwnProperty.call(req, 'files') && Object.prototype.hasOwnProperty.call(req, 'secretKey')) {
    if (fileMd5Err) {
      body.fileErr = [fileMd5Err]
    } else {
      try {
        body.files = encryptFiles(req.secretKey)
      } catch (err) {
        body.fileErr = [String(err && err.message ? err.message : err)]
      }
    }
  }
  plugin('ADW', Buffer.concat([Buffer.from([40]), mcString(JSON.stringify(body))]))
  const p = pos()
  emit({
    state: inGame ? '挂机中' : '进门中',
    detail: `已回门内校验 MD5×${fileMd5Count}${body.files ? '' : ' 无files'}`,
    x: p.x,
    y: p.y,
    z: p.z,
    phase: inGame ? 'portal' : 'join'
  })
}

try {
  buildFileMd5(gameDir())
  emit({ state: '进门中', detail: fileMd5Err ? `文件 MD5 失败: ${fileMd5Err}` : `文件 MD5 已备好 ${fileMd5Count} 个` })
} catch (err) {
  fileMd5Err = String(err && err.message ? err.message : err)
  emit({ state: '进门中', detail: `文件 MD5 失败: ${fileMd5Err}` })
}

function sendRegister () {
  const channels = ['FML|HS', 'FML', 'FML|MP', 'FORGE', 'MC|Brand', 'ADW']
  plugin('REGISTER', Buffer.from(channels.join('\0'), 'utf8'))
}

function sendModList () {
  const parts = [Buffer.from([2]), varint(mods.length)]
  for (const pair of mods) {
    const mid = Array.isArray(pair) ? pair[0] : pair.modid
    const ver = Array.isArray(pair) ? pair[1] : pair.version
    parts.push(mcString(mid), mcString(ver))
  }
  plugin('FML|HS', Buffer.from([1, 2]))
  plugin('FML|HS', Buffer.concat(parts))
  emit({ state: '进门中', detail: `FML ModList 已发送 (${mods.length})` })
}

function onFml (data) {
  if (!data || !data.length) return
  const disc = data[0]
  if (disc === 0) sendModList()
  else if (disc === 2) plugin('FML|HS', Buffer.from([255, 2]))
  else if (disc === 3) {
    plugin('FML|HS', Buffer.from([255, 3]))
    const hasMore = data.length === 2 ? !!data[1] : true
    if (!hasMore) plugin('FML|HS', Buffer.from([255, 4]))
  } else if (disc === 255 && data.length > 1 && data[1] < 5) {
    plugin('FML|HS', Buffer.from([255, Math.max(2, data[1])]))
  }
}

function claimAll () {
  if (!inGame) return
  emit({ state: '领奖中', detail: '发送在线领奖', x: pos().x, y: pos().y, z: pos().z, phase: 'portal' })
  for (let i = 0; i < 4; i++) {
    plugin('ADW', Buffer.concat([Buffer.from([22]), int32(i), Buffer.from([0])]))
    plugin('ADW', Buffer.concat([Buffer.from([22]), int32(i), Buffer.from([1])]))
  }
}

function int32 (n) {
  const b = Buffer.alloc(4)
  b.writeInt32BE(n, 0)
  return b
}

function pos () {
  const p = bot.entity && bot.entity.position
  if (!p) return { x: 0, y: 0, z: 0 }
  return { x: p.x, y: p.y, z: p.z }
}

const tx = Number(cfg.tx)
const ty = Number(cfg.ty)
const tz = Number(cfg.tz)
let startY = null
let startDim = null
let inGame = false
let walking = false
let claimTimer = null

const bot = mineflayer.createBot({
  host: cfg.host,
  port: Number(cfg.port),
  username: cfg.username,
  version: '1.12.2',
  auth: 'offline',
  hideErrors: true,
  checkTimeoutInterval: 120000,
  fakeHost: cfg.host + '\0FML\0',
  brand: 'fml,forge',
  viewDistance: 'normal'
})

bot.loadPlugin(pathfinder)

function markIn (reason) {
  if (inGame) return
  inGame = true
  const p = pos()
  emit({
    state: '挂机中',
    detail: `已进门(${reason}) ${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}`,
    x: p.x,
    y: p.y,
    z: p.z,
    phase: 'portal',
    online_ms: lastOnline
  })
  try { bot.pathfinder.setGoal(null) } catch (e) {}
  bot.clearControlStates()
  const sec = Math.max(15, Number(cfg.claimIntervalSec) || 45)
  claimTimer = setInterval(claimAll, sec * 1000)
  setTimeout(claimAll, 1500)
}

let lastOnline = null

function checkEnter () {
  const p = pos()
  const dim = bot.game && bot.game.dimension
  if (startY != null && p.y < startY - 2) markIn(`Y ${startY.toFixed(1)}→${p.y.toFixed(1)}`)
  else if (startDim != null && dim != null && dim !== startDim) markIn(`维度 ${startDim}→${dim}`)
}

function beginWalk () {
  if (walking) return
  walking = true
  const moves = new Movements(bot)
  moves.canDig = false
  moves.allow1by1towers = false
  moves.allowParkour = true
  moves.allowSprinting = false
  bot.pathfinder.setMovements(moves)
  bot.pathfinder.setGoal(new goals.GoalNear(tx, ty, tz, 1))
  const p = pos()
  const dist = Math.hypot(tx - p.x, tz - p.z)
  emit({
    state: '进门中',
    detail: `寻路前往世界之门 ${tx},${ty},${tz} 距${dist.toFixed(1)}`,
    x: p.x,
    y: p.y,
    z: p.z,
    phase: 'join'
  })
}

bot.once('login', () => {
  sendRegister()
  plugin('MC|Brand', mcString('fml,forge'))
  sendModList()
  emit({ state: '进门中', detail: '已进 Play，等待出生…' })
})

bot._client.on('custom_payload', (packet) => {
  const channel = packet.channel
  const data = packet.data || Buffer.alloc(0)
  if (channel === 'FML|HS') onFml(data)
  else if (channel === 'ADW' && data.length && data[0] === 39) replyInfo(data)
  else if (channel === 'ADW' && data.length >= 9 && data[0] === 21) {
    const ms = Number(data.readBigInt64BE(1))
    if (Number.isFinite(ms) && ms >= 0) lastOnline = ms
    const p = pos()
    emit({
      state: inGame ? '挂机中' : '进门中',
      detail: '收到 ADW 在线同步',
      x: p.x,
      y: p.y,
      z: p.z,
      phase: inGame ? 'portal' : 'join',
      online_ms: lastOnline
    })
    markIn('ADW')
  }
})

bot.once('spawn', () => {
  const p = pos()
  startY = p.y
  startDim = bot.game && bot.game.dimension
  emit({
    state: '进门中',
    detail: `出生 ${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}`,
    x: p.x,
    y: p.y,
    z: p.z,
    phase: 'join'
  })
  setTimeout(beginWalk, Math.max(0, Number(cfg.joinDelaySec) || 0) * 1000)
})

bot.on('respawn', () => {
  checkEnter()
})

let dropping = false
let fallY = 0
let fallVy = 0
let fallSnaps = 0

function dropIntoPortal (reason) {
  if (inGame || dropping || !bot.entity) return
  dropping = true
  try { bot.pathfinder.setGoal(null) } catch (e) {}
  bot.clearControlStates()
  bot.physicsEnabled = false
  const p = pos()
  fallY = p.y
  fallVy = 0
  const cx = Math.floor(tx) + 0.5
  const cz = Math.floor(tz) + 0.5
  emit({
    state: '进门中',
    detail: `到门口，按重力下落 (${reason}) ${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}`,
    x: p.x,
    y: p.y,
    z: p.z,
    phase: 'join'
  })
  const timer = setInterval(() => {
    if (inGame || !bot.entity) {
      clearInterval(timer)
      return
    }
    fallVy = (fallVy - 0.08) * 0.98
    fallY += fallVy
    const packet = { x: cx, y: fallY, z: cz, onGround: false }
    try {
      bot._client.write('position', packet)
      bot.entity.position.set(cx, fallY, cz)
      bot.entity.onGround = false
    } catch (e) {}
    checkEnter()
    if (fallY < (startY == null ? ty : startY) - 8) {
      clearInterval(timer)
      emit({
        state: '进门中',
        detail: `已下落到 Y=${fallY.toFixed(1)} 仍未传送（服务器拉回 ${fallSnaps} 次）`,
        x: cx,
        y: fallY,
        z: cz,
        phase: 'join'
      })
    }
  }, 50)
}

bot.on('forcedMove', () => {
  if (!dropping || inGame) return
  fallSnaps += 1
  const p = pos()
  fallY = p.y
  fallVy = 0
})

bot.on('goal_reached', () => {
  dropIntoPortal('到达门心')
})

bot.on('path_update', (r) => {
  if (!r || r.status !== 'noPath' || inGame) return
  emit({ state: '进门中', detail: '寻路无路，改为朝门心按住前进' })
  try { bot.pathfinder.setGoal(null) } catch (e) {}
  bot.lookAt(new Vec3(tx + 0.5, ty + 1.6, tz + 0.5), true).catch(() => {})
  bot.setControlState('forward', true)
  bot.setControlState('sprint', true)
})

setInterval(() => {
  if (!bot.entity) return
  checkEnter()
  if (inGame) return
  const p = pos()
  const dist = Math.hypot(tx - p.x, tz - p.z)
  if (dist < 1.6) dropIntoPortal('距门' + dist.toFixed(1))
  emit({
    state: '进门中',
    detail: `移动中 距门${dist.toFixed(1)} XYZ ${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}`,
    x: p.x,
    y: p.y,
    z: p.z,
    phase: 'live'
  })
}, 1000)

bot.on('kicked', (reason) => {
  const text = typeof reason === 'string' ? reason : JSON.stringify(reason)
  emit({ state: '异常', detail: `踢出: ${String(text).slice(0, 160)}` })
  process.exit(1)
})

bot.on('error', (err) => {
  emit({ state: '异常', detail: String(err && err.message ? err.message : err).slice(0, 160) })
})

bot.on('end', (reason) => {
  if (claimTimer) clearInterval(claimTimer)
  emit({ state: '进门中', detail: `连接结束 ${reason || ''}` })
  process.exit(inGame ? 0 : 1)
})

let stdinBuf = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (chunk) => {
  stdinBuf += chunk
  let nl
  while ((nl = stdinBuf.indexOf('\n')) >= 0) {
    const line = stdinBuf.slice(0, nl).trim()
    stdinBuf = stdinBuf.slice(nl + 1)
    if (line === 'STOP') {
      try { bot.quit('stop') } catch (e) {}
      process.exit(0)
    } else if (line === 'CLAIM') {
      claimAll()
    }
  }
})
