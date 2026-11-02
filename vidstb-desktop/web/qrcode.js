/* 极简二维码生成器（无依赖、不联网）。
 *
 * 只做手机访问这一件事：把一个 http://192.168.x.x:8777/ 这样的网址画成二维码，
 * 让手机扫一下就能打开。因此只实现必要的能力：
 *   - 8 位字节模式（byte mode），UTF-8
 *   - 纠错等级 M，版本 1~10（足够放下几百个字符的网址）
 *   - 输出布尔矩阵 / SVG 字符串
 *
 * 算法是标准的 QR 流程：数据编码 → Reed-Solomon 纠错 → 分块交织 →
 * 铺功能图形 → 之字形填数据 → 8 种掩码挑分数最低的。
 */
(function (global) {
  'use strict';

  // 版本 -> [总码字数, 每块纠错码字数, [[块数, 每块数据码字数], ...]]（纠错等级 M）
  var EC_M = {
    1: [26, 10, [[1, 16]]],
    2: [44, 16, [[1, 28]]],
    3: [70, 26, [[1, 44]]],
    4: [100, 18, [[2, 32]]],
    5: [134, 24, [[2, 43]]],
    6: [172, 16, [[4, 27]]],
    7: [196, 18, [[4, 31]]],
    8: [242, 22, [[2, 38], [2, 39]]],
    9: [292, 22, [[3, 36], [2, 37]]],
    10: [346, 26, [[4, 43], [1, 44]]]
  };

  // 各版本对齐图案的中心坐标
  var ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]
  };

  var MAX_VERSION = 10;
  var EC_LEVEL_BITS = 0;  // 纠错等级 M 的 2 位编码

  // ---- GF(256) 运算，本原多项式 0x11d ----
  var EXP = new Array(512);
  var LOG = new Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) { x ^= 0x11d; }
    }
    for (var j = 255; j < 512; j++) { EXP[j] = EXP[j - 255]; }
  })();

  function gfMul(a, b) {
    if (a === 0 || b === 0) { return 0; }
    return EXP[LOG[a] + LOG[b]];
  }

  function generatorPoly(degree) {
    var poly = [1];
    for (var d = 0; d < degree; d++) {
      var next = [];
      for (var i = 0; i < poly.length + 1; i++) { next.push(0); }
      for (var k = 0; k < poly.length; k++) {
        next[k] ^= poly[k];
        next[k + 1] ^= gfMul(poly[k], EXP[d]);
      }
      poly = next;
    }
    return poly;
  }

  function rsEncode(data, ecLength) {
    var gen = generatorPoly(ecLength);
    var buf = [];
    var i;
    for (i = 0; i < data.length + ecLength; i++) { buf.push(0); }
    for (i = 0; i < data.length; i++) { buf[i] = data[i]; }
    for (i = 0; i < data.length; i++) {
      var coef = buf[i];
      if (coef !== 0) {
        for (var j = 0; j < gen.length; j++) {
          buf[i + j] ^= gfMul(gen[j], coef);
        }
      }
    }
    return buf.slice(data.length);
  }

  // ---- 位缓冲 ----
  function BitBuffer() { this.bits = []; }
  BitBuffer.prototype.put = function (value, length) {
    for (var i = length - 1; i >= 0; i--) { this.bits.push((value >>> i) & 1); }
  };

  function utf8Bytes(text) {
    if (typeof TextEncoder !== 'undefined') {
      return Array.prototype.slice.call(new TextEncoder().encode(text));
    }
    var out = [];
    var encoded = unescape(encodeURIComponent(text));
    for (var i = 0; i < encoded.length; i++) { out.push(encoded.charCodeAt(i) & 0xff); }
    return out;
  }

  function dataCapacity(version) {
    var groups = EC_M[version][2];
    var total = 0;
    for (var i = 0; i < groups.length; i++) { total += groups[i][0] * groups[i][1]; }
    return total;
  }

  function pickVersion(byteLength) {
    for (var v = 1; v <= MAX_VERSION; v++) {
      var headerBits = 4 + (v < 10 ? 8 : 16);
      var usableBits = dataCapacity(v) * 8 - headerBits;
      if (byteLength * 8 <= usableBits) { return v; }
    }
    return 0;
  }

  function encodeCodewords(bytes, version) {
    var capacityBits = dataCapacity(version) * 8;
    var bb = new BitBuffer();
    bb.put(4, 4);                                   // 字节模式
    bb.put(bytes.length, version < 10 ? 8 : 16);    // 字符计数
    for (var i = 0; i < bytes.length; i++) { bb.put(bytes[i], 8); }

    var remaining = capacityBits - bb.bits.length;
    bb.put(0, Math.min(4, remaining));              // 结束符
    while (bb.bits.length % 8 !== 0) { bb.bits.push(0); }
    var pads = [0xEC, 0x11];
    var idx = 0;
    while (bb.bits.length < capacityBits) { bb.put(pads[idx % 2], 8); idx++; }

    var codewords = [];
    for (var p = 0; p < bb.bits.length; p += 8) {
      var value = 0;
      for (var b = 0; b < 8; b++) { value = (value << 1) | bb.bits[p + b]; }
      codewords.push(value & 0xff);
    }
    return codewords;
  }

  function interleave(codewords, version) {
    var spec = EC_M[version];
    var ecLength = spec[1];
    var blocks = [];
    var offset = 0;
    for (var g = 0; g < spec[2].length; g++) {
      var count = spec[2][g][0];
      var dataLen = spec[2][g][1];
      for (var n = 0; n < count; n++) {
        var data = codewords.slice(offset, offset + dataLen);
        offset += dataLen;
        blocks.push({ data: data, ec: rsEncode(data, ecLength) });
      }
    }
    var out = [];
    var maxData = 0;
    var maxEc = 0;
    for (var i = 0; i < blocks.length; i++) {
      maxData = Math.max(maxData, blocks[i].data.length);
      maxEc = Math.max(maxEc, blocks[i].ec.length);
    }
    var k, b2;
    for (k = 0; k < maxData; k++) {
      for (b2 = 0; b2 < blocks.length; b2++) {
        if (k < blocks[b2].data.length) { out.push(blocks[b2].data[k]); }
      }
    }
    for (k = 0; k < maxEc; k++) {
      for (b2 = 0; b2 < blocks.length; b2++) {
        if (k < blocks[b2].ec.length) { out.push(blocks[b2].ec[k]); }
      }
    }
    return out;
  }

  // ---- 矩阵 ----
  function makeGrid(size, value) {
    var grid = [];
    for (var r = 0; r < size; r++) {
      var row = [];
      for (var c = 0; c < size; c++) { row.push(value); }
      grid.push(row);
    }
    return grid;
  }

  function placeFinder(modules, reserved, size, row, col) {
    for (var r = -1; r <= 7; r++) {
      for (var c = -1; c <= 7; c++) {
        var rr = row + r;
        var cc = col + c;
        if (rr < 0 || rr >= size || cc < 0 || cc >= size) { continue; }
        var dark = (r >= 0 && r <= 6 && (c === 0 || c === 6)) ||
                   (c >= 0 && c <= 6 && (r === 0 || r === 6)) ||
                   (r >= 2 && r <= 4 && c >= 2 && c <= 4);
        modules[rr][cc] = dark;
        reserved[rr][cc] = true;
      }
    }
  }

  function placeAlignment(modules, reserved, size, coords) {
    var i, j;
    for (i = 0; i < coords.length; i++) {
      for (j = 0; j < coords.length; j++) {
        var row = coords[i];
        var col = coords[j];
        // 与三个定位图案重叠的位置不画
        if ((row <= 8 && col <= 8) || (row <= 8 && col >= size - 9) ||
            (row >= size - 9 && col <= 8)) {
          continue;
        }
        // 5x5：最外圈深色、第二圈浅色、中心 1 格深色
        for (var r = -2; r <= 2; r++) {
          for (var c = -2; c <= 2; c++) {
            var dist = Math.max(Math.abs(r), Math.abs(c));
            modules[row + r][col + c] = dist !== 1;
            reserved[row + r][col + c] = true;
          }
        }
      }
    }
  }

  function reserveFormat(modules, reserved, size) {
    var i;
    for (i = 0; i <= 8; i++) {
      if (!reserved[8][i]) { reserved[8][i] = true; }
      if (!reserved[i][8]) { reserved[i][8] = true; }
    }
    for (i = 0; i < 8; i++) {
      reserved[8][size - 1 - i] = true;
      reserved[size - 1 - i][8] = true;
    }
    reserved[size - 8][8] = true;   // 固定黑点
  }

  function reserveVersion(modules, reserved, size) {
    var r, c;
    for (r = 0; r < 6; r++) {
      for (c = size - 11; c <= size - 9; c++) { reserved[r][c] = true; }
    }
    for (c = 0; c < 6; c++) {
      for (r = size - 11; r <= size - 9; r++) { reserved[r][c] = true; }
    }
  }

  function formatBits(mask) {
    var data = (EC_LEVEL_BITS << 3) | mask;
    var rem = data;
    for (var i = 0; i < 10; i++) { rem = (rem << 1) ^ ((rem >>> 9) * 0x537); }
    return ((data << 10) | rem) ^ 0x5412;
  }

  function versionBits(version) {
    var rem = version;
    for (var i = 0; i < 12; i++) { rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25); }
    return (version << 12) | rem;
  }

  function writeFormat(modules, size, bits) {
    var i;
    // 左下那份：第 8 列往下、第 8 行往左（bit 0 在 (0,8)）
    for (i = 0; i <= 5; i++) { modules[i][8] = ((bits >>> i) & 1) === 1; }
    modules[7][8] = ((bits >>> 6) & 1) === 1;
    modules[8][8] = ((bits >>> 7) & 1) === 1;
    modules[8][7] = ((bits >>> 8) & 1) === 1;
    for (i = 9; i < 15; i++) { modules[8][14 - i] = ((bits >>> i) & 1) === 1; }
    // 右上那份
    for (i = 0; i < 8; i++) { modules[8][size - 1 - i] = ((bits >>> i) & 1) === 1; }
    for (i = 8; i < 15; i++) { modules[size - 15 + i][8] = ((bits >>> i) & 1) === 1; }
    modules[size - 8][8] = true;   // 固定深色模块
  }

  function writeVersion(modules, size, version) {
    if (version < 7) { return; }
    var bits = versionBits(version);
    var i;
    for (i = 0; i < 18; i++) {
      var value = ((bits >>> i) & 1) === 1;
      var offset = size - 11 + (i % 3);
      var line = Math.floor(i / 3);
      modules[line][offset] = value;      // 右上
      modules[offset][line] = value;      // 左下
    }
  }

  function maskBit(mask, i, j) {
    switch (mask) {
      case 0: return (i + j) % 2 === 0;
      case 1: return i % 2 === 0;
      case 2: return j % 3 === 0;
      case 3: return (i + j) % 3 === 0;
      case 4: return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0;
      case 5: return ((i * j) % 2) + ((i * j) % 3) === 0;
      case 6: return (((i * j) % 2) + ((i * j) % 3)) % 2 === 0;
      default: return (((i + j) % 2) + ((i * j) % 3)) % 2 === 0;
    }
  }

  // 规则 3 用的游程历史：runHistory[0] 是最近一段的长度
  function addHistory(runHistory, length, size) {
    var value = length;
    if (runHistory[0] === 0) { value += size; }   // 首段前面补上“边框”留白
    for (var i = runHistory.length - 1; i > 0; i--) { runHistory[i] = runHistory[i - 1]; }
    runHistory[0] = value;
  }

  function countFinderPatterns(runHistory) {
    var n = runHistory[1];
    var core = n > 0 && runHistory[2] === n && runHistory[3] === n * 3 &&
               runHistory[4] === n && runHistory[5] === n;
    var head = (core && runHistory[0] >= n * 4 && runHistory[6] >= n) ? 1 : 0;
    var tail = (core && runHistory[6] >= n * 4 && runHistory[0] >= n) ? 1 : 0;
    return head + tail;
  }

  function terminateHistory(runHistory, isDark, length, size) {
    if (isDark) {
      addHistory(runHistory, length, size);
      length = 0;
    }
    addHistory(runHistory, length + size, size);
    return countFinderPatterns(runHistory);
  }

  function linePenalty(get, size) {
    var score = 0;
    var runColor = false;
    var runLength = 0;
    var history = [0, 0, 0, 0, 0, 0, 0];
    for (var i = 0; i < size; i++) {
      if (get(i) === runColor) {
        runLength++;
        if (runLength === 5) { score += 3; }        // 规则 1
        else if (runLength > 5) { score += 1; }
      } else {
        addHistory(history, runLength, size);
        if (!runColor) { score += countFinderPatterns(history) * 40; }
        runColor = get(i);
        runLength = 1;
      }
    }
    score += terminateHistory(history, runColor, runLength, size) * 40;
    return score;
  }

  function penalty(modules, size) {
    var score = 0;
    var r, c;

    // 规则 1 + 规则 3：按行、按列各扫一遍
    for (r = 0; r < size; r++) {
      score += linePenalty(bindRow(modules, r), size);
    }
    for (c = 0; c < size; c++) {
      score += linePenalty(bindCol(modules, c), size);
    }

    // 规则 2：2x2 同色块
    for (r = 0; r < size - 1; r++) {
      for (c = 0; c < size - 1; c++) {
        var v = modules[r][c];
        if (v === modules[r][c + 1] && v === modules[r + 1][c] && v === modules[r + 1][c + 1]) {
          score += 3;
        }
      }
    }

    // 规则 4：黑白比例偏离 50%
    var dark = 0;
    for (r = 0; r < size; r++) {
      for (c = 0; c < size; c++) { if (modules[r][c]) { dark++; } }
    }
    var total = size * size;
    var k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
    return score + k * 10;
  }

  function bindRow(modules, row) {
    return function (i) { return modules[row][i]; };
  }

  function bindCol(modules, col) {
    return function (i) { return modules[i][col]; };
  }

  /** 生成布尔矩阵（true = 深色） */
  function matrix(text) {
    var bytes = utf8Bytes(String(text == null ? '' : text));
    var version = pickVersion(bytes.length);
    if (!version) { throw new Error('内容太长，二维码放不下'); }

    var size = version * 4 + 17;
    var codewords = interleave(encodeCodewords(bytes, version), version);

    var modules = makeGrid(size, false);
    var reserved = makeGrid(size, false);

    placeFinder(modules, reserved, size, 0, 0);
    placeFinder(modules, reserved, size, 0, size - 7);
    placeFinder(modules, reserved, size, size - 7, 0);

    for (var i = 8; i < size - 8; i++) {
      var timing = i % 2 === 0;
      modules[6][i] = timing; reserved[6][i] = true;
      modules[i][6] = timing; reserved[i][6] = true;
    }
    placeAlignment(modules, reserved, size, ALIGN[version]);
    reserveFormat(modules, reserved, size);
    if (version >= 7) { reserveVersion(modules, reserved, size); }

    // 之字形填数据
    var bits = [];
    for (var w = 0; w < codewords.length; w++) {
      for (var b = 7; b >= 0; b--) { bits.push((codewords[w] >>> b) & 1); }
    }
    var bitIndex = 0;
    for (var right = size - 1; right >= 1; right -= 2) {
      if (right === 6) { right = 5; }
      for (var vert = 0; vert < size; vert++) {
        for (var j = 0; j < 2; j++) {
          var x = right - j;
          var upward = ((right + 1) & 2) === 0;
          var y = upward ? size - 1 - vert : vert;
          if (!reserved[y][x]) {
            modules[y][x] = bitIndex < bits.length ? bits[bitIndex] === 1 : false;
            bitIndex++;
          }
        }
      }
    }

    // 挑分数最低的掩码
    var best = null;
    var bestScore = Infinity;
    for (var mask = 0; mask < 8; mask++) {
      var candidate = makeGrid(size, false);
      for (var rr = 0; rr < size; rr++) {
        for (var cc = 0; cc < size; cc++) {
          var value = modules[rr][cc];
          if (!reserved[rr][cc] && maskBit(mask, rr, cc)) { value = !value; }
          candidate[rr][cc] = value;
        }
      }
      writeFormat(candidate, size, formatBits(mask));
      writeVersion(candidate, size, version);
      var score = penalty(candidate, size);
      if (score < bestScore) { bestScore = score; best = candidate; }
    }
    return best;
  }

  /** 生成 SVG 字符串；scale 为每格像素，margin 为静区格数 */
  function svg(text, options) {
    var opts = options || {};
    var scale = opts.scale || 6;
    var margin = opts.margin == null ? 2 : opts.margin;
    var grid = matrix(text);
    var size = grid.length;
    var dim = (size + margin * 2) * scale;
    var dark = opts.dark || '#11181f';
    var light = opts.light || '#ffffff';
    var path = '';
    for (var r = 0; r < size; r++) {
      for (var c = 0; c < size; c++) {
        if (!grid[r][c]) { continue; }
        var x = (c + margin) * scale;
        var y = (r + margin) * scale;
        path += 'M' + x + ' ' + y + 'h' + scale + 'v' + scale + 'h-' + scale + 'z';
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + dim + '" height="' + dim +
      '" viewBox="0 0 ' + dim + ' ' + dim + '" shape-rendering="crispEdges">' +
      '<rect width="100%" height="100%" fill="' + light + '"/>' +
      '<path d="' + path + '" fill="' + dark + '"/></svg>';
  }

  global.QRCode = {
    matrix: matrix,
    svg: svg,
    versionOf: function (text) { return pickVersion(utf8Bytes(String(text)).length); },
    // 调试用：只看编码后的码字（数据 + 纠错，已交织），方便和参考实现核对
    codewords: function (text) {
      var bytes = utf8Bytes(String(text == null ? '' : text));
      var version = pickVersion(bytes.length);
      return { version: version, codewords: interleave(encodeCodewords(bytes, version), version) };
    },
  };
})(typeof window !== 'undefined' ? window : this);
