// MD5 implementation — used to compute the Subsonic salted-token
// (`token = md5(password + salt)`) entirely in the webview so the
// password never crosses the wire after the login page accepts it.
//
// MD5 isn't part of WebCrypto, so we implement it here. RFC 1321,
// adapted from Joseph Myers' well-known public-domain implementation
// at https://www.myersdaily.org/joseph/javascript/md5-text.html —
// reformatted for readability + ES modules. All bit-twiddling logic
// is unchanged from the reference.
//
// Hashing the password is not a security boundary (Subsonic's token
// scheme is auth-on-the-wire, not at-rest); it's just what every
// Subsonic client does. For at-rest, the OS keychain via the host
// store plugin is the real protection.

export function md5(input) {
  return rhex(md51(input));
}

function safeAdd(a, b) {
  const lsw = (a & 0xffff) + (b & 0xffff);
  const msw = (a >> 16) + (b >> 16) + (lsw >> 16);
  return (msw << 16) | (lsw & 0xffff);
}

function rotateLeft(x, n) {
  return (x << n) | (x >>> (32 - n));
}

function md5cmn(q, a, b, x, s, t) {
  return safeAdd(rotateLeft(safeAdd(safeAdd(a, q), safeAdd(x, t)), s), b);
}
function md5ff(a, b, c, d, x, s, t) {
  return md5cmn((b & c) | (~b & d), a, b, x, s, t);
}
function md5gg(a, b, c, d, x, s, t) {
  return md5cmn((b & d) | (c & ~d), a, b, x, s, t);
}
function md5hh(a, b, c, d, x, s, t) {
  return md5cmn(b ^ c ^ d, a, b, x, s, t);
}
function md5ii(a, b, c, d, x, s, t) {
  return md5cmn(c ^ (b | ~d), a, b, x, s, t);
}

function md51(s) {
  const txt = unescape(encodeURIComponent(s)); // UTF-8
  const n = txt.length;
  const state = [1732584193, -271733879, -1732584194, 271733878];
  let i;
  for (i = 64; i <= n; i += 64) {
    md5cycle(state, md5blk(txt.substring(i - 64, i)));
  }
  const tail = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const remaining = txt.substring(i - 64);
  for (let j = 0; j < remaining.length; j++) {
    tail[j >> 2] |= remaining.charCodeAt(j) << ((j % 4) << 3);
  }
  tail[remaining.length >> 2] |= 0x80 << ((remaining.length % 4) << 3);
  if (remaining.length > 55) {
    md5cycle(state, tail);
    for (let j = 0; j < 16; j++) tail[j] = 0;
  }
  // Append length in bits (low 32 then high 32; we only have low here).
  tail[14] = n * 8;
  md5cycle(state, tail);
  return state;
}

function md5blk(s) {
  const blk = new Array(16);
  for (let i = 0; i < 64; i += 4) {
    blk[i >> 2] =
      s.charCodeAt(i) +
      (s.charCodeAt(i + 1) << 8) +
      (s.charCodeAt(i + 2) << 16) +
      (s.charCodeAt(i + 3) << 24);
  }
  return blk;
}

function md5cycle(state, x) {
  let [a, b, c, d] = state;

  a = md5ff(a, b, c, d, x[0], 7, -680876936);
  d = md5ff(d, a, b, c, x[1], 12, -389564586);
  c = md5ff(c, d, a, b, x[2], 17, 606105819);
  b = md5ff(b, c, d, a, x[3], 22, -1044525330);
  a = md5ff(a, b, c, d, x[4], 7, -176418897);
  d = md5ff(d, a, b, c, x[5], 12, 1200080426);
  c = md5ff(c, d, a, b, x[6], 17, -1473231341);
  b = md5ff(b, c, d, a, x[7], 22, -45705983);
  a = md5ff(a, b, c, d, x[8], 7, 1770035416);
  d = md5ff(d, a, b, c, x[9], 12, -1958414417);
  c = md5ff(c, d, a, b, x[10], 17, -42063);
  b = md5ff(b, c, d, a, x[11], 22, -1990404162);
  a = md5ff(a, b, c, d, x[12], 7, 1804603682);
  d = md5ff(d, a, b, c, x[13], 12, -40341101);
  c = md5ff(c, d, a, b, x[14], 17, -1502002290);
  b = md5ff(b, c, d, a, x[15], 22, 1236535329);

  a = md5gg(a, b, c, d, x[1], 5, -165796510);
  d = md5gg(d, a, b, c, x[6], 9, -1069501632);
  c = md5gg(c, d, a, b, x[11], 14, 643717713);
  b = md5gg(b, c, d, a, x[0], 20, -373897302);
  a = md5gg(a, b, c, d, x[5], 5, -701558691);
  d = md5gg(d, a, b, c, x[10], 9, 38016083);
  c = md5gg(c, d, a, b, x[15], 14, -660478335);
  b = md5gg(b, c, d, a, x[4], 20, -405537848);
  a = md5gg(a, b, c, d, x[9], 5, 568446438);
  d = md5gg(d, a, b, c, x[14], 9, -1019803690);
  c = md5gg(c, d, a, b, x[3], 14, -187363961);
  b = md5gg(b, c, d, a, x[8], 20, 1163531501);
  a = md5gg(a, b, c, d, x[13], 5, -1444681467);
  d = md5gg(d, a, b, c, x[2], 9, -51403784);
  c = md5gg(c, d, a, b, x[7], 14, 1735328473);
  b = md5gg(b, c, d, a, x[12], 20, -1926607734);

  a = md5hh(a, b, c, d, x[5], 4, -378558);
  d = md5hh(d, a, b, c, x[8], 11, -2022574463);
  c = md5hh(c, d, a, b, x[11], 16, 1839030562);
  b = md5hh(b, c, d, a, x[14], 23, -35309556);
  a = md5hh(a, b, c, d, x[1], 4, -1530992060);
  d = md5hh(d, a, b, c, x[4], 11, 1272893353);
  c = md5hh(c, d, a, b, x[7], 16, -155497632);
  b = md5hh(b, c, d, a, x[10], 23, -1094730640);
  a = md5hh(a, b, c, d, x[13], 4, 681279174);
  d = md5hh(d, a, b, c, x[0], 11, -358537222);
  c = md5hh(c, d, a, b, x[3], 16, -722521979);
  b = md5hh(b, c, d, a, x[6], 23, 76029189);
  a = md5hh(a, b, c, d, x[9], 4, -640364487);
  d = md5hh(d, a, b, c, x[12], 11, -421815835);
  c = md5hh(c, d, a, b, x[15], 16, 530742520);
  b = md5hh(b, c, d, a, x[2], 23, -995338651);

  a = md5ii(a, b, c, d, x[0], 6, -198630844);
  d = md5ii(d, a, b, c, x[7], 10, 1126891415);
  c = md5ii(c, d, a, b, x[14], 15, -1416354905);
  b = md5ii(b, c, d, a, x[5], 21, -57434055);
  a = md5ii(a, b, c, d, x[12], 6, 1700485571);
  d = md5ii(d, a, b, c, x[3], 10, -1894986606);
  c = md5ii(c, d, a, b, x[10], 15, -1051523);
  b = md5ii(b, c, d, a, x[1], 21, -2054922799);
  a = md5ii(a, b, c, d, x[8], 6, 1873313359);
  d = md5ii(d, a, b, c, x[15], 10, -30611744);
  c = md5ii(c, d, a, b, x[6], 15, -1560198380);
  b = md5ii(b, c, d, a, x[13], 21, 1309151649);
  a = md5ii(a, b, c, d, x[4], 6, -145523070);
  d = md5ii(d, a, b, c, x[11], 10, -1120210379);
  c = md5ii(c, d, a, b, x[2], 15, 718787259);
  b = md5ii(b, c, d, a, x[9], 21, -343485551);

  state[0] = safeAdd(state[0], a);
  state[1] = safeAdd(state[1], b);
  state[2] = safeAdd(state[2], c);
  state[3] = safeAdd(state[3], d);
}

function rhex(state) {
  const hex = "0123456789abcdef";
  let out = "";
  for (let i = 0; i < 4; i++) {
    const word = state[i];
    for (let j = 0; j < 4; j++) {
      const byte = (word >> (j * 8)) & 0xff;
      out += hex[byte >> 4] + hex[byte & 0xf];
    }
  }
  return out;
}
