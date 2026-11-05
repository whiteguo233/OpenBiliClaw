// QR L profiles match qrcode.base.rs_blocks / util.pattern_position.
const QR_PROFILES = [
  { version: 5, data: 108, ecc: 26, blocks: [108], alignment: [6, 30] },
  { version: 10, data: 274, ecc: 18, blocks: [68, 68, 69, 69], alignment: [6, 28, 50] },
];
const QR_EC_LEVEL_L = 1;
const QR_MASK_PATTERN = 0;
const QR_PAD_BYTES = [0xec, 0x11];

const GF_EXP = new Array(512).fill(0);
const GF_LOG = new Array(256).fill(0);

{
  let x = 1;
  for (let i = 0; i < 255; i += 1) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if ((x & 0x100) !== 0) x ^= 0x11d;
  }
  for (let i = 255; i < GF_EXP.length; i += 1) {
    GF_EXP[i] = GF_EXP[i - 255];
  }
}

export function buildMobileWebUrl({ scheme, host, port } = {}) {
  const safeScheme = scheme === "https" ? "https" : "http";
  const safeHost = String(host || "127.0.0.1").trim() || "127.0.0.1";
  const urlHost = safeHost.includes(":") && !safeHost.startsWith("[") ? `[${safeHost}]` : safeHost;
  const safePort = Number.isInteger(Number(port)) && String(port).trim() !== "" ? Number(port) : 8420;
  return `${safeScheme}://${urlHost}:${safePort}/m/`;
}

export function isLoopbackMobileHost(host) {
  const value = String(host || "").trim().toLowerCase();
  return (
    value === "" ||
    value === "localhost" ||
    value === "::1" ||
    value === "[::1]" ||
    value.startsWith("127.")
  );
}

export function getMobileQrViewState(endpoint = {}) {
  const url = buildMobileWebUrl(endpoint);
  const loopback = isLoopbackMobileHost(endpoint.host);
  return {
    url,
    tone: loopback ? "warning" : "info",
    hint: loopback
      ? "当前后端地址是本机地址，手机通常打不开。把插件设置里的后端地址改成电脑局域网 IP，并用 --host 0.0.0.0 启动后再扫。"
      : "手机和这台电脑连在同一个局域网时，可以直接扫码打开移动端 Web。",
  };
}

export function createQrSvgMarkup(text, { moduleSize = 5, quietZone = 4 } = {}) {
  const matrix = createQrMatrix(String(text || ""));
  const totalModules = matrix.length + quietZone * 2;
  const pixelSize = totalModules * moduleSize;
  const path = matrixToPath(matrix, quietZone);
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${totalModules} ${totalModules}" width="${pixelSize}" height="${pixelSize}" role="img" aria-label="移动端页面二维码">`,
    `<rect width="${totalModules}" height="${totalModules}" fill="#fff"/>`,
    `<path d="${path}" fill="#20304a"/>`,
    "</svg>",
  ].join("");
}

function createQrMatrix(text) {
  const bytes = Array.from(new TextEncoder().encode(text));
  const profile = QR_PROFILES.find((item) => bytes.length * 8 + 4 + (item.version < 10 ? 8 : 16) <= item.data * 8);
  if (!profile) throw new Error("mobile web URL is too long for QR code");
  const data = encodeDataCodewords(bytes, profile);
  const blocks = [];
  const parity = [];
  let offset = 0;
  for (const count of profile.blocks) {
    const block = data.slice(offset, offset + count);
    offset += count;
    blocks.push(block);
    parity.push(reedSolomonRemainder(block, profile.ecc));
  }
  const codewords = [];
  for (let i = 0; i < Math.max(...profile.blocks); i += 1) {
    for (const block of blocks) if (i < block.length) codewords.push(block[i]);
  }
  for (let i = 0; i < profile.ecc; i += 1) for (const block of parity) codewords.push(block[i]);
  const size = profile.version * 4 + 17;
  const modules = Array.from({ length: size }, () => new Array(size).fill(false));
  const reserved = Array.from({ length: size }, () => new Array(size).fill(false));
  drawFunctionPatterns(modules, reserved, profile);
  drawCodewords(modules, reserved, codewords);
  drawFormatBits(modules, reserved);
  return modules;
}

function encodeDataCodewords(bytes, profile) {
  const bits = [];
  appendBits(bits, 0b0100, 4);
  appendBits(bits, bytes.length, profile.version < 10 ? 8 : 16);
  for (const byte of bytes) appendBits(bits, byte, 8);
  const capacityBits = profile.data * 8;
  appendBits(bits, 0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);
  const data = [];
  for (let i = 0; i < bits.length; i += 8) {
    let value = 0;
    for (let j = 0; j < 8; j += 1) value = (value << 1) | bits[i + j];
    data.push(value);
  }
  for (let i = 0; data.length < profile.data; i += 1) data.push(QR_PAD_BYTES[i % QR_PAD_BYTES.length]);
  return data;
}

function appendBits(bits, value, length) {
  for (let i = length - 1; i >= 0; i -= 1) {
    bits.push((value >>> i) & 1);
  }
}

function drawFunctionPatterns(modules, reserved, profile) {
  const QR_SIZE = modules.length;
  drawFinder(modules, reserved, 0, 0);
  drawFinder(modules, reserved, QR_SIZE - 7, 0);
  drawFinder(modules, reserved, 0, QR_SIZE - 7);
  const positions = profile.alignment;
  for (let row = 0; row < positions.length; row += 1) {
    for (let col = 0; col < positions.length; col += 1) {
      if ((row === 0 && (col === 0 || col === positions.length - 1))
          || (row === positions.length - 1 && col === 0)) continue;
      drawAlignment(modules, reserved, positions[col], positions[row]);
    }
  }
  if (profile.version >= 7) {
    let remainder = profile.version;
    for (let i = 0; i < 12; i += 1) remainder = (remainder << 1) ^ ((remainder >>> 11) * 0x1f25);
    const bits = (profile.version << 12) | remainder;
    for (let i = 0; i < 18; i += 1) {
      const a = QR_SIZE - 11 + i % 3;
      const b = Math.floor(i / 3);
      setFunctionModule(modules, reserved, a, b, getBit(bits, i));
      setFunctionModule(modules, reserved, b, a, getBit(bits, i));
    }
  }

  for (let i = 0; i < QR_SIZE; i += 1) {
    if (!reserved[6][i]) setFunctionModule(modules, reserved, i, 6, i % 2 === 0);
    if (!reserved[i][6]) setFunctionModule(modules, reserved, 6, i, i % 2 === 0);
  }

  setFunctionModule(modules, reserved, 8, QR_SIZE - 8, true);
  drawFormatBits(modules, reserved);
}

function drawFinder(modules, reserved, left, top) {
  const QR_SIZE = modules.length;
  for (let dy = -1; dy <= 7; dy += 1) {
    for (let dx = -1; dx <= 7; dx += 1) {
      const x = left + dx;
      const y = top + dy;
      if (x < 0 || y < 0 || x >= QR_SIZE || y >= QR_SIZE) continue;
      const inFinder = dx >= 0 && dx <= 6 && dy >= 0 && dy <= 6;
      const dark = inFinder
        && (dx === 0 || dx === 6 || dy === 0 || dy === 6 || (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4));
      setFunctionModule(modules, reserved, x, y, dark);
    }
  }
}

function drawAlignment(modules, reserved, centerX, centerY) {
  for (let dy = -2; dy <= 2; dy += 1) {
    for (let dx = -2; dx <= 2; dx += 1) {
      const distance = Math.max(Math.abs(dx), Math.abs(dy));
      setFunctionModule(modules, reserved, centerX + dx, centerY + dy, distance !== 1);
    }
  }
}

function drawCodewords(modules, reserved, codewords) {
  const QR_SIZE = modules.length;
  const bits = [];
  for (const codeword of codewords) appendBits(bits, codeword, 8);

  let bitIndex = 0;
  let upward = true;
  for (let right = QR_SIZE - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vertical = 0; vertical < QR_SIZE; vertical += 1) {
      const y = upward ? QR_SIZE - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const x = right - offset;
        if (reserved[y][x]) continue;
        const bit = bitIndex < bits.length ? bits[bitIndex] === 1 : false;
        modules[y][x] = bit !== shouldApplyMask(x, y);
        bitIndex += 1;
      }
    }
    upward = !upward;
  }
}

function shouldApplyMask(x, y) {
  return (x + y) % 2 === QR_MASK_PATTERN;
}

function drawFormatBits(modules, reserved) {
  const QR_SIZE = modules.length;
  const bits = getFormatBits(QR_EC_LEVEL_L, QR_MASK_PATTERN);
  for (let i = 0; i <= 5; i += 1) setFunctionModule(modules, reserved, 8, i, getBit(bits, i));
  setFunctionModule(modules, reserved, 8, 7, getBit(bits, 6));
  setFunctionModule(modules, reserved, 8, 8, getBit(bits, 7));
  setFunctionModule(modules, reserved, 7, 8, getBit(bits, 8));
  for (let i = 9; i < 15; i += 1) setFunctionModule(modules, reserved, 14 - i, 8, getBit(bits, i));

  for (let i = 0; i < 8; i += 1) setFunctionModule(modules, reserved, QR_SIZE - 1 - i, 8, getBit(bits, i));
  for (let i = 8; i < 15; i += 1) setFunctionModule(modules, reserved, 8, QR_SIZE - 15 + i, getBit(bits, i));
  setFunctionModule(modules, reserved, 8, QR_SIZE - 8, true);
}

function getFormatBits(ecLevel, maskPattern) {
  const data = (ecLevel << 3) | maskPattern;
  let bits = data << 10;
  for (let i = 14; i >= 10; i -= 1) {
    if (((bits >>> i) & 1) !== 0) bits ^= 0x537 << (i - 10);
  }
  return ((data << 10) | bits) ^ 0x5412;
}

function getBit(value, index) {
  return ((value >>> index) & 1) !== 0;
}

function setFunctionModule(modules, reserved, x, y, dark) {
  modules[y][x] = dark;
  reserved[y][x] = true;
}

function gfMul(a, b) {
  if (a === 0 || b === 0) return 0;
  return GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

function reedSolomonGenerator(degree) {
  let result = [1];
  for (let i = 0; i < degree; i += 1) {
    const next = new Array(result.length + 1).fill(0);
    for (let j = 0; j < result.length; j += 1) {
      next[j] ^= result[j];
      next[j + 1] ^= gfMul(result[j], GF_EXP[i]);
    }
    result = next;
  }
  return result.slice(1);
}

function reedSolomonRemainder(data, degree) {
  const generator = reedSolomonGenerator(degree);
  const result = new Array(degree).fill(0);
  for (const byte of data) {
    const factor = byte ^ result.shift();
    result.push(0);
    for (let i = 0; i < degree; i += 1) {
      result[i] ^= gfMul(generator[i], factor);
    }
  }
  return result;
}

function matrixToPath(matrix, quietZone) {
  const commands = [];
  for (let y = 0; y < matrix.length; y += 1) {
    for (let x = 0; x < matrix[y].length; x += 1) {
      if (matrix[y][x]) commands.push(`M${x + quietZone} ${y + quietZone}h1v1h-1z`);
    }
  }
  return commands.join("");
}
