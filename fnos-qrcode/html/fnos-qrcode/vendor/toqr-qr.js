/* ToQR 内置二维码编码器 —— QR Code Model 2，字节模式（UTF-8），纠错等级 M
 * 纯前端：不联网、不依赖任何第三方库。用法：ToQR.dataUrl(text, 400) -> data:image/png
 */
var ToQR = (function () {
  'use strict';

  // 版本 → [每块纠错码字, 组1块数, 组1数据码字, 组2块数, 组2数据码字]
  var BLOCKS_M = [
    [10, 1, 16, 0, 0], [16, 1, 28, 0, 0], [26, 1, 44, 0, 0], [18, 2, 32, 0, 0],
    [24, 2, 43, 0, 0], [16, 4, 27, 0, 0], [18, 4, 31, 0, 0], [22, 2, 38, 2, 39],
    [22, 3, 36, 2, 37], [26, 4, 43, 1, 44], [30, 1, 50, 4, 51], [22, 6, 36, 2, 37],
    [22, 8, 37, 1, 38], [24, 4, 40, 5, 41], [24, 5, 41, 5, 42], [28, 7, 45, 3, 46],
    [28, 10, 46, 1, 47], [26, 9, 43, 4, 44], [26, 3, 44, 11, 45], [26, 3, 41, 13, 42],
    [26, 17, 42, 0, 0], [28, 17, 46, 0, 0], [28, 4, 47, 14, 48], [28, 6, 45, 14, 46],
    [28, 8, 47, 13, 48], [28, 19, 46, 4, 47], [28, 22, 45, 3, 46], [28, 3, 45, 23, 46],
    [28, 21, 45, 7, 46], [28, 19, 47, 10, 48], [28, 2, 46, 29, 47], [28, 10, 46, 23, 47],
    [28, 14, 46, 21, 47], [28, 14, 46, 23, 47], [28, 12, 47, 26, 48], [28, 6, 47, 34, 48],
    [28, 29, 46, 14, 47], [28, 13, 46, 32, 47], [28, 40, 47, 7, 48], [28, 18, 47, 31, 48]
  ];
  var MAX_VERSION = 40;
  var QUIET = 4;

  var EXP = new Uint8Array(512);
  var LOG = new Uint8Array(256);
  (function () {
    var x = 1, i;
    for (i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11D;
    }
    for (i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  })();

  function gfMul(a, b) {
    return (a === 0 || b === 0) ? 0 : EXP[LOG[a] + LOG[b]];
  }

  function dataCodewords(version) {
    var b = BLOCKS_M[version - 1];
    return b[1] * b[2] + b[3] * b[4];
  }

  function eccFor(data, eccLen) {
    var gen = [1], i, j;
    for (i = 0; i < eccLen; i++) {
      var next = new Array(gen.length + 1);
      for (j = 0; j < next.length; j++) next[j] = 0;
      for (j = 0; j < gen.length; j++) {
        next[j] ^= gen[j];
        next[j + 1] ^= gfMul(gen[j], EXP[i]);
      }
      gen = next;
    }
    var res = data.slice();
    for (i = 0; i < eccLen; i++) res.push(0);
    for (i = 0; i < data.length; i++) {
      var coef = res[i];
      if (coef) {
        for (j = 0; j < gen.length; j++) res[i + j] ^= gfMul(gen[j], coef);
      }
    }
    return res.slice(data.length);
  }

  function alignmentPositions(version) {
    if (version === 1) return [];
    var size = version * 4 + 17;
    var count = Math.floor(version / 7) + 2;
    var step = version === 32 ? 26
      : Math.floor((version * 4 + count * 2 + 1) / (count * 2 - 2)) * 2;
    var pos = [6];
    for (var i = count - 2; i >= 0; i--) pos.push(size - 7 - i * step);
    return pos;
  }

  function encodeCodewords(bytes, version) {
    var capBits = dataCodewords(version) * 8;
    var ccBits = version < 10 ? 8 : 16;
    var out = [], buf = 0, buflen = 0;

    function put(val, len) {
      for (var i = len - 1; i >= 0; i--) {
        buf = (buf << 1) | ((val >> i) & 1);
        buflen++;
        if (buflen === 8) { out.push(buf & 0xFF); buf = 0; buflen = 0; }
      }
    }

    put(0b0100, 4);
    put(bytes.length, ccBits);
    for (var i = 0; i < bytes.length; i++) put(bytes[i], 8);

    var used = 4 + ccBits + 8 * bytes.length;
    put(0, Math.min(4, capBits - used));
    if (buflen > 0) put(0, 8 - buflen);

    var pad = [0xEC, 0x11], k = 0;
    while (out.length < capBits / 8) { out.push(pad[k & 1]); k++; }

    var b = BLOCKS_M[version - 1];
    var eccLen = b[0], blocks = [], pos = 0, n;
    for (n = 0; n < b[1]; n++) { blocks.push(out.slice(pos, pos + b[2])); pos += b[2]; }
    for (n = 0; n < b[3]; n++) { blocks.push(out.slice(pos, pos + b[4])); pos += b[4]; }

    var eccs = blocks.map(function (blk) { return eccFor(blk, eccLen); });
    var merged = [], longest = 0, m;
    for (m = 0; m < blocks.length; m++) longest = Math.max(longest, blocks[m].length);
    for (i = 0; i < longest; i++) {
      for (m = 0; m < blocks.length; m++) {
        if (i < blocks[m].length) merged.push(blocks[m][i]);
      }
    }
    for (i = 0; i < eccLen; i++) {
      for (m = 0; m < eccs.length; m++) merged.push(eccs[m][i]);
    }
    return merged;
  }

  function drawFormat(mod, func, mask) {
    var size = mod.length;
    var data = mask;            // 纠错等级 M 的两位是 00
    var rem = data, i;
    for (i = 0; i < 10; i++) rem = (rem << 1) ^ (((rem >> 9) & 1) * 0x537);
    var bits = ((data << 10) | rem) ^ 0x5412;

    function put(r, c, bit) {
      mod[r][c] = (bits >> bit) & 1;
      func[r][c] = 1;
    }
    for (i = 0; i <= 5; i++) put(i, 8, i);
    put(7, 8, 6);
    put(8, 8, 7);
    put(8, 7, 8);
    for (i = 9; i < 15; i++) put(8, 14 - i, i);
    for (i = 0; i < 8; i++) put(8, size - 1 - i, i);
    for (i = 8; i < 15; i++) put(size - 15 + i, 8, i);
    put(size - 8, 8, 0);
    mod[size - 8][8] = 1;
  }

  function buildMatrix(version, codewords) {
    var size = version * 4 + 17, r, c, i, j, dr, dc;
    var mod = [], func = [];
    for (r = 0; r < size; r++) {
      mod.push(new Uint8Array(size));
      func.push(new Uint8Array(size));
    }
    function put(rr, cc, dark) {
      mod[rr][cc] = dark ? 1 : 0;
      func[rr][cc] = 1;
    }

    var corners = [[0, 0], [0, size - 7], [size - 7, 0]];
    for (i = 0; i < corners.length; i++) {
      var br = corners[i][0], bc = corners[i][1];
      for (dr = -1; dr <= 7; dr++) {
        for (dc = -1; dc <= 7; dc++) {
          r = br + dr; c = bc + dc;
          if (r < 0 || r >= size || c < 0 || c >= size) continue;
          var inside = dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6;
          var dark = inside && (dr === 0 || dr === 6 || dc === 0 || dc === 6
            || (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4));
          put(r, c, dark);
        }
      }
    }

    for (i = 8; i < size - 8; i++) {
      put(6, i, i % 2 === 0);
      put(i, 6, i % 2 === 0);
    }

    var pos = alignmentPositions(version);
    var last = pos.length ? pos[pos.length - 1] : -99;
    for (i = 0; i < pos.length; i++) {
      for (j = 0; j < pos.length; j++) {
        r = pos[i]; c = pos[j];
        if ((r === 6 && c === 6) || (r === 6 && c === last) || (r === last && c === 6)) continue;
        for (dr = -2; dr <= 2; dr++) {
          for (dc = -2; dc <= 2; dc++) {
            put(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
          }
        }
      }
    }

    drawFormat(mod, func, 0);

    if (version >= 7) {
      var rem = version;
      for (i = 0; i < 12; i++) rem = (rem << 1) ^ (((rem >> 11) & 1) * 0x1F25);
      var vbits = (version << 12) | rem;
      for (i = 0; i < 18; i++) {
        var bit = (vbits >> i) & 1;
        var a = size - 11 + i % 3, b2 = Math.floor(i / 3);
        put(b2, a, bit);
        put(a, b2, bit);
      }
    }

    var idx = 0, totalBits = codewords.length * 8, right = size - 1;
    while (right >= 1) {
      if (right === 6) right = 5;
      for (var vert = 0; vert < size; vert++) {
        for (j = 0; j < 2; j++) {
          c = right - j;
          var upward = ((right + 1) & 2) === 0;
          r = upward ? size - 1 - vert : vert;
          if (!func[r][c] && idx < totalBits) {
            mod[r][c] = (codewords[idx >> 3] >> (7 - (idx & 7))) & 1;
            idx++;
          }
        }
      }
      right -= 2;
    }
    return { mod: mod, func: func };
  }

  function applyMask(mod, func, mask) {
    var size = mod.length, r, c, invert;
    for (r = 0; r < size; r++) {
      for (c = 0; c < size; c++) {
        if (func[r][c]) continue;
        if (mask === 0) invert = (r + c) % 2 === 0;
        else if (mask === 1) invert = r % 2 === 0;
        else if (mask === 2) invert = c % 3 === 0;
        else if (mask === 3) invert = (r + c) % 3 === 0;
        else if (mask === 4) invert = (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
        else if (mask === 5) invert = (r * c) % 2 + (r * c) % 3 === 0;
        else if (mask === 6) invert = ((r * c) % 2 + (r * c) % 3) % 2 === 0;
        else invert = ((r + c) % 2 + (r * c) % 3) % 2 === 0;
        if (invert) mod[r][c] ^= 1;
      }
    }
  }

  function penalty(mod) {
    var size = mod.length, score = 0, r, c, i, run, line;

    function scanLine(get) {
      var s = 0;
      run = 1;
      for (var k = 1; k < size; k++) {
        if (get(k) === get(k - 1)) run++;
        else { if (run >= 5) s += 3 + (run - 5); run = 1; }
      }
      if (run >= 5) s += 3 + (run - 5);
      return s;
    }

    for (r = 0; r < size; r++) score += scanLine(function (k) { return mod[r][k]; });
    for (c = 0; c < size; c++) score += scanLine(function (k) { return mod[k][c]; });

    for (r = 0; r < size - 1; r++) {
      for (c = 0; c < size - 1; c++) {
        var v = mod[r][c];
        if (v === mod[r][c + 1] && v === mod[r + 1][c] && v === mod[r + 1][c + 1]) score += 3;
      }
    }

    var pa = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];
    var pb = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
    function findPattern(get) {
      var s = 0;
      for (var k = 0; k + 11 <= size; k++) {
        var okA = true, okB = true;
        for (var t = 0; t < 11; t++) {
          var val = get(k + t);
          if (val !== pa[t]) okA = false;
          if (val !== pb[t]) okB = false;
          if (!okA && !okB) break;
        }
        if (okA || okB) s += 40;
      }
      return s;
    }
    for (r = 0; r < size; r++) score += findPattern(function (k) { return mod[r][k]; });
    for (c = 0; c < size; c++) score += findPattern(function (k) { return mod[k][c]; });

    var dark = 0;
    for (r = 0; r < size; r++) for (c = 0; c < size; c++) dark += mod[r][c];
    var total = size * size;
    score += Math.floor(Math.abs(dark * 20 - total * 10) / total) * 10;
    return score;
  }

  function modules(text) {
    var bytes = new TextEncoder().encode(String(text));
    var version = 0, v, ccBits;
    for (v = 1; v <= MAX_VERSION; v++) {
      ccBits = v < 10 ? 8 : 16;
      if (4 + ccBits + 8 * bytes.length <= dataCodewords(v) * 8) { version = v; break; }
    }
    if (!version) throw new Error('内容过长');

    var cw = encodeCodewords(bytes, version);
    var built = buildMatrix(version, cw);
    var best = null, bestScore = Infinity;
    for (var mask = 0; mask < 8; mask++) {
      var trial = built.mod.map(function (row) { return new Uint8Array(row); });
      applyMask(trial, built.func, mask);
      drawFormat(trial, built.func, mask);
      var s = penalty(trial);
      if (s < bestScore) { bestScore = s; best = trial; }
    }
    return best;
  }

  function dataUrl(text, size) {
    var mod = modules(text);
    var count = mod.length + QUIET * 2;
    var scale = Math.max(1, Math.floor((size || 400) / count));
    var edge = count * scale;
    var canvas = document.createElement('canvas');
    canvas.width = edge;
    canvas.height = edge;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, edge, edge);
    ctx.fillStyle = '#000';
    for (var r = 0; r < mod.length; r++) {
      for (var c = 0; c < mod.length; c++) {
        if (mod[r][c]) {
          ctx.fillRect((QUIET + c) * scale, (QUIET + r) * scale, scale, scale);
        }
      }
    }
    return canvas.toDataURL('image/png');
  }

  return { modules: modules, dataUrl: dataUrl };
})();
