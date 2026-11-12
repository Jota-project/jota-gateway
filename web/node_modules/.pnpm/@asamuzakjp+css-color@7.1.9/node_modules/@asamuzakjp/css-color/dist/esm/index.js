import { SyntaxFlag as e, color as t } from "@csstools/css-color-parser";
import { isCommentNode as n, isFunctionNode as r, isWhitespaceNode as i, parseComponentValue as a, parseListOfComponentValues as o } from "@csstools/css-parser-algorithms";
import { TokenType as s, tokenize as c } from "@csstools/css-tokenizer";
import { LRUCache as l } from "lru-cache";
import { calc as u } from "@csstools/css-calc";
// src/utils/cache.ts
var d = 4096, f = class {
	#e;
	constructor(e) {
		this.#e = e;
	}
	get item() {
		return this.#e;
	}
}, p = new l({ max: d }), m = (e, t) => {
	e && (t instanceof f ? p.set(e, t) : p.set(e, new f(t)));
}, h = (e) => {
	if (!e) return !1;
	let t = p.get(e);
	return t !== void 0 && t;
}, g = (e) => {
	let t = Object.keys(e);
	if (t.length === 0) return "";
	t.sort();
	let n = "";
	for (let r of t) n += `${r}:${JSON.stringify(e[r])};`;
	return n;
}, _ = (e, t = {}) => {
	if (!e || t.customProperty && typeof t.customProperty.callback == "function" || t.dimension && typeof t.dimension.callback == "function") return "";
	let n = e.namespace || "", r = e.name || "", i = e.value || "";
	if (!n && !r && !i) return "";
	let a = `${n}:${r}:${i}`, o = [];
	for (let [e, n] of Object.entries(t)) e !== "customProperty" && e !== "dimension" && o.push(`${e}_${n}`);
	let s = t.customProperty ? g(t.customProperty) : "", c = t.dimension ? g(t.dimension) : "";
	return `${a}:${o.sort().join("|")}:${s}:${c}`;
}, v = (e) => typeof e == "string" || e instanceof String, y = (e) => v(e) || typeof e == "number", b = "(?:0|[1-9]\\d*)", x = "clamp|max|min|exp|hypot|log|pow|sqrt|abs|sign|mod|rem|round|a?(?:cos|sin|tan)|atan2", S = `calc|${x}`, C = `var|${S}`, w = "deg|g?rad|turn", T = "[cm]m|[dls]?v(?:[bhiw]|max|min)|in|p[ctx]|q|r?(?:[cl]h|cap|e[mx]|ic)", E = `[+-]?(?:${b}(?:\\.\\d*)?|\\.\\d+)(?:e-?${b})?`, D = `\\+?(?:${b}(?:\\.\\d*)?|\\.\\d+)(?:e-?${b})?`, O = "none", k = `${E}%`, A = `^(?:${S})\\(|(?<=[*\\/\\s\\(])(?:${S})\\(`, j = `^(?:${x})\\($`, ee = "^var\\(|(?<=[*\\/\\s\\(])var\\(", te = `^(?:${C})\\(`, M = `(?:\\s*\\/\\s*(?:${E}|${k}|${O}))?`, ne = `(?:\\s*,\\s*(?:${E}|${k}))?`, re = "[a-z]+|#[\\da-f]{3}|#[\\da-f]{4}|#[\\da-f]{6}|#[\\da-f]{8}", ie = "(?:ok)?lch|hsl|hwb", ae = "(?:de|in)creasing|longer|shorter", oe = `${E}(?:${w})?`, se = `(?:${E}(?:${w})?|${O})`, ce = `(?:${E}|${k}|${O})`, le = "(?:ok)?l(?:ab|ch)|color|hsla?|hwb|rgba?", ue = `(?:${ie})(?:\\s(?:${ae})\\shue)?`, de = "(?:ok)?lab", fe = "(?:a98|prophoto)-rgb|display-p3|rec2020|srgb(?:-linear)?", pe = "xyz(?:-d(?:50|65))?", me = `${de}|${fe}|${pe}`, he = `${ue}|${me}`, ge = "calc(", _e = `(?:${fe}|${pe})(?:\\s+${ce}){3}${M}`, ve = "^light-dark\\(", ye = `^(?:${le})\\(\\s*from\\s+|(?<=[\\s])(?:${le})\\(\\s*from\\s+`, be = `${se}(?:\\s+${ce}){2}${M}`, xe = `${oe}(?:\\s*,\\s*${k}){2}${ne}`, Se = `(?:${ce}\\s+){2}${se}${M}`, Ce = `${ce}(?:\\s+${ce}){2}${M}`, we = `${re}|hsla?\\(\\s*${xe}\\s*\\)|rgba?\\(\\s*${`(?:${E}(?:\\s*,\\s*${E}){2}|${k}(?:\\s*,\\s*${k}){2})${ne}`}\\s*\\)|(?:hsla?|hwb)\\(\\s*${be}\\s*\\)|(?:(?:ok)?lab|rgba?)\\(\\s*${Ce}\\s*\\)|(?:ok)?lch\\(\\s*${Se}\\s*\\)|color\\(\\s*${_e}\\s*\\)`, Te = `(?:${we})(?:\\s+${k})?`, Ee = `color-mix\\((?:\\s*in\\s+(?:${he})\\s*,)?(?:\\s*${Te}\\s*,)*\\s*${Te}\\s*\\)`, De = "(?:repeating-)?(?:conic|linear|radial)-gradient", N = "computedValue", P = "mixValue", Oe = "specifiedValue", ke = .5, Ae = 24389 / 27, je = 12.92, F = .055, Me = 1024, Ne = 65536, Pe = 2.4, Fe = .001, Ie = "util", { CloseParen: Le, Comma: Re, Comment: ze, Delim: Be, EOF: Ve, Function: He, OpenParen: Ue, Whitespace: We } = s;
RegExp(`^(${E})(${w})?$`);
var Ge = /^,$/, Ke = /--[\w-]+/g, qe = /^\/$/, Je = /^\s+$/, Ye = /* @__PURE__ */ new Map([
	["xx-small", 9 / 16],
	["x-small", 5 / 8],
	["small", 13 / 16],
	["medium", 1],
	["large", 9 / 8],
	["x-large", 3 / 2],
	["xx-large", 2],
	["xxx-large", 3]
]), Xe = /* @__PURE__ */ new Map([["smaller", 1 / 1.2], ["larger", 1.2]]), Ze = /* @__PURE__ */ new Map([
	["cm", 96 / 2.54],
	["mm", 96 / 25.4],
	["q", 96 / 101.6],
	["in", 96],
	["pc", 16],
	["pt", 96 / 72],
	["px", 1]
]), Qe = /* @__PURE__ */ new Map([
	["rcap", 1],
	["rch", .5],
	["rem", 1],
	["rex", .5],
	["ric", 1],
	["rlh", 1.2]
]), $e = (e = {}, t = !1) => {
	let { maxLength: n } = e, r = t ? Ne : Me;
	return Number.isInteger(n) && n > 0 && n < r ? n : r;
}, I = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let n = e.trim(), r = { ...t }, i = $e(r, !0);
	if (!n || n.length > i) return [];
	let { delimiter: a = " ", preserveComment: o = !1 } = r, s = _({
		namespace: Ie,
		name: "splitValue",
		value: n
	}, {
		delimiter: a,
		preserveComment: o
	}), l = h(s);
	if (l) return [...l.item];
	let u;
	switch (a) {
		case ",":
			u = Ge;
			break;
		case "/":
			u = qe;
			break;
		default: u = Je;
	}
	let d = c({ css: n }), f = 0, p = "", g = [];
	for (let [e, t] of d) switch (e) {
		case Re:
		case Be:
			f === 0 && u.test(t) ? (g.push(p.trim()), p = "") : p += t;
			break;
		case ze:
			o && (a === "," || a === "/") && (p += t);
			break;
		case He:
		case Ue:
			p += t, f++;
			break;
		case Le:
			p += t, f--;
			break;
		case We:
			u.test(t) ? f === 0 ? p &&= (g.push(p.trim()), "") : p += " " : p.endsWith(" ") || (p += " ");
			break;
		default: e === Ve ? (g.push(p.trim()), p = "") : p += t;
	}
	return m(s, g), [...g];
}, et = (e) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let t = e.trim();
	if (!t) return [];
	let n = _({
		namespace: Ie,
		name: "extractDashedIdent",
		value: t
	}), r = h(n);
	if (r) return [...r.item];
	let i = t.match(Ke), a = i ? [...new Set(i)] : [];
	return m(n, a), [...a];
}, L = (e, t = 0) => {
	if (!Number.isFinite(e)) throw TypeError(`${e} is not a finite number.`);
	if (!Number.isFinite(t)) throw TypeError(`${t} is not a finite number.`);
	if (t < 0 || t > 16) throw RangeError(`${t} is not between 0 and 16.`);
	if (t === 0) return Math.round(e);
	let n = t === 16 ? 6 : t < 10 ? 4 : 5;
	return parseFloat(e.toPrecision(n));
}, tt = (e, t, n = "shorter") => {
	if (!Number.isFinite(e)) throw TypeError(`${e} is not a finite number.`);
	if (!Number.isFinite(t)) throw TypeError(`${t} is not a finite number.`);
	let r = e, i = t;
	switch (n) {
		case "decreasing":
			i > r && (r += 360);
			break;
		case "increasing":
			i < r && (i += 360);
			break;
		case "longer":
			i > r && i < r + 180 ? r += 360 : i > r - 180 && i <= r && (i += 360);
			break;
		default: i > r + 180 ? r += 360 : i < r - 180 && (i += 360);
	}
	return [r, i];
}, nt = (e, t, n = {}) => {
	let { dimension: r = {} } = n, { callback: i, em: a, rem: o, vh: s, vw: c } = r;
	if (v(e)) {
		let t = e.toLowerCase().trim(), r = $e(n);
		if (t.length > r) return NaN;
		let i = Ye.get(t);
		if (i !== void 0 && typeof o == "number") return i * o;
		let s = Xe.get(t);
		return s !== void 0 && typeof a == "number" ? s * a : NaN;
	}
	if (Number.isFinite(e) && t) {
		let n = t.toLowerCase();
		if (Object.hasOwn(r, n)) {
			let t = r[n];
			return typeof t == "number" ? e * t : NaN;
		}
		if (typeof i == "function") {
			let t = i(n);
			return typeof t == "number" ? e * t : NaN;
		}
		let l = Ze.get(n);
		if (l !== void 0) return e * l;
		let u = Qe.get(n);
		if (u !== void 0 && typeof o == "number") return e * u * o;
		let d = Qe.get(`r${n}`);
		if (d !== void 0 && typeof a == "number") return e * d * a;
		switch (n) {
			case "vb": return typeof s == "number" ? e * s : NaN;
			case "vi": return typeof c == "number" ? e * c : NaN;
			case "vmax": return typeof s == "number" && typeof c == "number" ? e * Math.max(s, c) : NaN;
			case "vmin": return typeof s == "number" && typeof c == "number" ? e * Math.min(s, c) : NaN;
		}
	}
	return NaN;
}, rt = (e) => {
	if (!Number.isFinite(e)) throw TypeError(`${e} is not a number.`);
	let t = Math.round(e);
	if (t < 0 || t > 255) throw RangeError(`${t} is not between 0 and 255.`);
	let n = t.toString(16);
	return n.length === 1 ? `0${n}` : n;
}, R = (e = "") => {
	if (!v(e)) return 1;
	let t = e.trim();
	if (!t) return 1;
	if (t === "none") return 0;
	let n;
	if (n = t.endsWith("%") ? parseFloat(t) / 100 : parseFloat(t), !Number.isFinite(n)) throw TypeError(`${n} is not a finite number.`);
	return n < .001 ? 0 : n > 1 ? 1 : parseFloat(n.toFixed(3));
}, it = (e) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let t = e.trim();
	if (!t) throw SyntaxError("Invalid property value: (empty string)");
	let n = parseInt(t, 16);
	if (n <= 0) return 0;
	if (n >= 255) return 1;
	let r = /* @__PURE__ */ new Map();
	for (let e = 1; e < 100; e++) r.set(Math.round(e * 255 / 100), e);
	return n = r.has(n) ? r.get(n) / 100 : Math.round(n / 255 / Fe) * Fe, parseFloat(n.toFixed(3));
}, at = (e) => {
	let n = c({ css: e });
	return t(a(n)) || null;
}, ot = (e, t, n = !1) => {
	if (t === "specifiedValue") return m(e, ""), "";
	if (n) return m(e, null), null;
	let r = [
		"rgb",
		0,
		0,
		0,
		0
	];
	return m(e, r), r;
}, z = (e, t = !1) => {
	switch (e) {
		case "hsl":
		case "hwb":
		case P: return null;
		case Oe: return "";
		default: return t ? null : [
			"rgb",
			0,
			0,
			0,
			0
		];
	}
}, st = (e) => {
	let t = o(c({ css: e })), r = [];
	for (let e of t) !n(e) && !i(e) && r.push(e.toString().trim());
	return r;
}, ct = [
	.3457 / .3585,
	1,
	.2958 / .3585
], lt = [
	[
		.955473421488075,
		-.02309845494876471,
		.06325924320057072
	],
	[
		-.0283697093338637,
		1.0099953980813041,
		.021041441191917323
	],
	[
		.012314014864481998,
		-.020507649298898964,
		1.330365926242124
	]
], B = [
	[
		1.0479297925449969,
		.022946870601609652,
		-.05019226628920524
	],
	[
		.02962780877005599,
		.9904344267538799,
		-.017073799063418826
	],
	[
		-.009243040646204504,
		.015055191490298152,
		.7518742814281371
	]
], ut = [
	[
		506752 / 1228815,
		87881 / 245763,
		12673 / 70218
	],
	[
		87098 / 409605,
		175762 / 245763,
		12673 / 175545
	],
	[
		7918 / 409605,
		87881 / 737289,
		1001167 / 1053270
	]
], dt = [
	[
		12831 / 3959,
		-329 / 214,
		-1974 / 3959
	],
	[
		-851781 / 878810,
		1648619 / 878810,
		36519 / 878810
	],
	[
		705 / 12673,
		-2585 / 12673,
		705 / 667
	]
], ft = [
	[
		.819022437996703,
		.3619062600528904,
		-.1288737815209879
	],
	[
		.0329836539323885,
		.9292868615863434,
		.0361446663506424
	],
	[
		.0481771893596242,
		.2642395317527308,
		.6335478284694309
	]
], pt = [
	[
		1.2268798758459243,
		-.5578149944602171,
		.2813910456659647
	],
	[
		-.0405757452148008,
		1.112286803280317,
		-.0717110580655164
	],
	[
		-.0763729366746601,
		-.4214933324022432,
		1.5869240198367816
	]
], mt = [
	[
		1,
		.3963377773761749,
		.2158037573099136
	],
	[
		1,
		-.1055613458156586,
		-.0638541728258133
	],
	[
		1,
		-.0894841775298119,
		-1.2914855480194092
	]
], ht = [
	[
		.210454268309314,
		.7936177747023054,
		-.0040720430116193
	],
	[
		1.9779985324311684,
		-2.42859224204858,
		.450593709617411
	],
	[
		.0259040424655478,
		.7827717124575296,
		-.8086757549230774
	]
], gt = [
	[
		608311 / 1250200,
		189793 / 714400,
		198249 / 1000160
	],
	[
		35783 / 156275,
		247089 / 357200,
		198249 / 2500400
	],
	[
		0,
		32229 / 714400,
		5220557 / 5000800
	]
], _t = [
	[
		63426534 / 99577255,
		20160776 / 139408157,
		47086771 / 278816314
	],
	[
		26158966 / 99577255,
		472592308 / 697040785,
		8267143 / 139408157
	],
	[
		0,
		19567812 / 697040785,
		295819943 / 278816314
	]
], vt = [
	[
		573536 / 994567,
		263643 / 1420810,
		187206 / 994567
	],
	[
		591459 / 1989134,
		6239551 / 9945670,
		374412 / 4972835
	],
	[
		53769 / 1989134,
		351524 / 4972835,
		4929758 / 4972835
	]
], yt = [
	[
		.7977666449006423,
		.13518129740053308,
		.0313477341283922
	],
	[
		.2880748288194013,
		.711835234241873,
		8993693872564e-17
	],
	[
		0,
		0,
		.8251046025104602
	]
], V = (e, t = {}) => {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	let { alpha: n = !1, minLength: r = 3, maxLength: i = 4, minRange: a = 0, maxRange: o = 1, validateRange: s = !0 } = t;
	if (!Number.isFinite(r)) throw TypeError(`${r} is not a number.`);
	if (!Number.isFinite(i)) throw TypeError(`${i} is not a number.`);
	if (!Number.isFinite(a)) throw TypeError(`${a} is not a number.`);
	if (!Number.isFinite(o)) throw TypeError(`${o} is not a number.`);
	let c = e.length;
	if (c < r || c > i) throw Error(`Unexpected array length ${c}.`);
	let l = 0;
	for (; l < c;) {
		let t = e[l];
		if (!Number.isFinite(t)) throw TypeError(`${t} is not a number.`);
		if (l < 3 && s && (t < a || t > o)) throw RangeError(`${t} is not between ${a} and ${o}.`);
		if (l === 3 && (t < 0 || t > 1)) throw RangeError(`${t} is not between 0 and 1.`);
		l++;
	}
	return n && c === 3 && e.push(1), e;
}, bt = (e, t, n = !1) => {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	if (e.length !== 4) throw Error(`Unexpected array length ${e.length}.`);
	if (!Array.isArray(t)) throw TypeError(`${t} is not an array.`);
	if (t.length !== 4) throw Error(`Unexpected array length ${t.length}.`);
	let r = 0;
	for (; r < 4;) e[r] === "none" && t[r] === "none" ? (e[r] = 0, t[r] = 0) : e[r] === "none" ? e[r] = t[r] : t[r] === "none" && (t[r] = e[r]), r++;
	return n ? [e, t] : [V(e, {
		minLength: 4,
		validateRange: !1
	}), V(t, {
		minLength: 4,
		validateRange: !1
	})];
}, H = (e, t, n = !1) => {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	if (e.length !== 3) throw Error(`Unexpected array length ${e.length}.`);
	if (!n) for (let t of e) t = V(t, {
		maxLength: 3,
		validateRange: !1
	});
	let [[r, i, a], [o, s, c], [l, u, d]] = e, f, p, m;
	return n ? [f, p, m] = t : [f, p, m] = V(t, {
		maxLength: 3,
		validateRange: !1
	}), [
		r * f + i * p + a * m,
		o * f + s * p + c * m,
		l * f + u * p + d * m
	];
}, xt = (e, t = !1) => {
	let n, r, i;
	t ? [n, r, i] = e : [n, r, i] = V(e, {
		maxLength: 3,
		maxRange: 255
	});
	let a = n / 255, o = r / 255, s = i / 255, c = .04045;
	return a > c ? a = ((a + F) / (1 + F)) ** +Pe : a /= je, o > c ? o = ((o + F) / (1 + F)) ** +Pe : o /= je, s > c ? s = ((s + F) / (1 + F)) ** +Pe : s /= je, [
		a,
		o,
		s
	];
}, St = (e, t = !1) => (t || (e = V(e, {
	maxLength: 3,
	maxRange: 255
})), e = xt(e, !0), H(ut, e, !0)), Ct = (e, t = !1) => {
	let [n, r, i] = V(e, { maxLength: 3 }), a = 809 / 258400;
	return n > a ? n = n ** (1 / Pe) * (1 + F) - F : n *= je, n *= 255, r > a ? r = r ** (1 / Pe) * (1 + F) - F : r *= je, r *= 255, i > a ? i = i ** (1 / Pe) * (1 + F) - F : i *= je, i *= 255, [
		t ? Math.round(n) : n,
		t ? Math.round(r) : r,
		t ? Math.round(i) : i
	];
}, wt = (e, t = !1) => {
	t || (e = V(e, {
		maxLength: 3,
		validateRange: !1
	}));
	let [n, r, i] = H(dt, e, !0);
	return [n, r, i] = Ct([
		Math.min(Math.max(n, 0), 1),
		Math.min(Math.max(r, 0), 1),
		Math.min(Math.max(i, 0), 1)
	], !0), [
		n,
		r,
		i
	];
}, Tt = (e, t = !1) => {
	let [n, r, i] = wt(e, t), a = n / 255, o = r / 255, s = i / 255, c = Math.max(a, o, s), l = Math.min(a, o, s), u = c - l, d = (c + l) * ke * 100, f, p;
	if (Math.round(d) === 0 || Math.round(d) === 100) f = 0, p = 0;
	else if (p = u / (1 - Math.abs(c + l - 1)) * 100, p === 0) f = 0;
	else {
		switch (c) {
			case a:
				f = (o - s) / u;
				break;
			case o:
				f = (s - a) / u + 2;
				break;
			case s:
			default: f = (a - o) / u + 4;
		}
		f = f * 60 % 360, f < 0 && (f += 360);
	}
	return [
		f,
		p,
		d
	];
}, Et = (e, t = !1) => {
	let [n, r, i] = wt(e, t), a = Math.min(n, r, i) / 255, o = 1 - Math.max(n, r, i) / 255, s;
	return a + o === 1 ? s = 0 : [s] = Tt(e), [
		s,
		a * 100,
		o * 100
	];
}, Dt = (e, t = !1) => {
	t || (e = V(e, {
		maxLength: 3,
		validateRange: !1
	}));
	let [n, r, i] = H(ht, H(ft, e, !0).map((e) => Math.cbrt(e)), !0);
	n = Math.min(Math.max(n, 0), 1);
	let a = Math.round(parseFloat(n.toFixed(4)) * 100);
	return (a === 0 || a === 100) && (r = 0, i = 0), [
		n,
		r,
		i
	];
}, Ot = (e, t = !1) => {
	let [n, r, i] = Dt(e, t), a, o, s = Math.round(parseFloat(n.toFixed(4)) * 100);
	return s === 0 || s === 100 ? (a = 0, o = 0) : (a = Math.max(Math.sqrt(r ** 2 + i ** 2), 0), parseFloat(a.toFixed(4)) === 0 ? o = 0 : (o = Math.atan2(i, r) * 180 / Math.PI, o < 0 && (o += 360))), [
		n,
		a,
		o
	];
}, kt = (e, t = !1) => (t || (e = V(e, {
	maxLength: 3,
	validateRange: !1
})), wt(H(lt, e, !0), !0)), At = (e, t = !1) => {
	t || (e = V(e, {
		maxLength: 3,
		validateRange: !1
	}));
	let [n, r, i] = e.map((e, t) => e / ct[t]).map((e) => e > .008856451679035631 ? Math.cbrt(e) : (Ae * e + 16) / 116), a = Math.min(Math.max(116 * r - 16, 0), 100), o, s;
	return a === 0 || a === 100 ? (o = 0, s = 0) : (o = (n - r) * 500, s = (r - i) * 200), [
		a,
		o,
		s
	];
}, jt = (e, t = !1) => {
	let [n, r, i] = At(e, t), a, o;
	return n === 0 || n === 100 ? (a = 0, o = 0) : (a = Math.max(Math.sqrt(r ** 2 + i ** 2), 0), o = Math.atan2(i, r) * 180 / Math.PI, o < 0 && (o += 360)), [
		n,
		a,
		o
	];
}, Mt = (e) => {
	let [t, n, r, i] = V(e, {
		alpha: !0,
		maxRange: 255
	}), a = rt(t), o = rt(n), s = rt(r), c = rt(i * 255);
	return c === "ff" ? `#${a}${o}${s}` : `#${a}${o}${s}${c}`;
}, Nt = (e) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	if (e = e.toLowerCase().trim(), !(/^#[\da-f]{6}$/.test(e) || /^#[\da-f]{3}$/.test(e) || /^#[\da-f]{8}$/.test(e) || /^#[\da-f]{4}$/.test(e))) throw SyntaxError(`Invalid property value: ${e}`);
	let t = [];
	if (/^#[\da-f]{3}$/.test(e)) {
		let [, n, r, i] = e.match(/^#([\da-f])([\da-f])([\da-f])$/);
		t.push(parseInt(`${n}${n}`, 16), parseInt(`${r}${r}`, 16), parseInt(`${i}${i}`, 16), 1);
	} else if (/^#[\da-f]{4}$/.test(e)) {
		let [, n, r, i, a] = e.match(/^#([\da-f])([\da-f])([\da-f])([\da-f])$/);
		t.push(parseInt(`${n}${n}`, 16), parseInt(`${r}${r}`, 16), parseInt(`${i}${i}`, 16), it(`${a}${a}`));
	} else if (/^#[\da-f]{8}$/.test(e)) {
		let [, n, r, i, a] = e.match(/^#([\da-f]{2})([\da-f]{2})([\da-f]{2})([\da-f]{2})$/);
		t.push(parseInt(n, 16), parseInt(r, 16), parseInt(i, 16), it(a));
	} else {
		let [, n, r, i] = e.match(/^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/);
		t.push(parseInt(n, 16), parseInt(r, 16), parseInt(i, 16), 1);
	}
	return t;
}, Pt = (e) => {
	let [t, n, r, i] = Nt(e), [a, o, s] = xt([
		t,
		n,
		r
	], !0);
	return [
		a,
		o,
		s,
		i
	];
}, Ft = (e) => {
	let [t, n, r, i] = Pt(e), [a, o, s] = H(ut, [
		t,
		n,
		r
	], !0);
	return [
		a,
		o,
		s,
		i
	];
}, { CloseParen: It, Comment: Lt, Delim: Rt, Dimension: zt, EOF: Bt, Function: Vt, Ident: Ht, Number: Ut, OpenParen: Wt, Whitespace: Gt } = s, Kt = "css-calc-var", qt = /* @__PURE__ */ new Set([
	"inherit",
	"initial",
	"revert",
	"revert-layer",
	"unset"
]), Jt = /* @__PURE__ */ new Set([
	"+",
	"-",
	"*",
	"/"
]), Yt = new RegExp(A, "i"), Xt = RegExp(`^calc\\((${E})\\)$`, "i"), Zt = new RegExp(j, "i"), Qt = new RegExp(ee, "i"), $t = new RegExp(te, "i"), en = /\s[-+*/]\s/, tn = (e) => {
	if (typeof e == "number") return {
		num: e,
		unit: ""
	};
	if (!v(e)) return null;
	let t = c({ css: e }).filter(([e]) => e !== Gt && e !== Lt && e !== Bt);
	if (t.length !== 1) return null;
	let [n, r, , , i] = t[0];
	return n === Ut ? {
		num: Number(r),
		unit: ""
	} : n === zt && i ? {
		num: Number(i.value),
		unit: i.unit
	} : r.endsWith("%") ? {
		num: parseFloat(r),
		unit: "%"
	} : null;
}, nn = class {
	#e;
	#t;
	#n;
	#r;
	#i;
	#a;
	#o;
	#s;
	#c;
	#l;
	#u;
	#d;
	#f;
	#p;
	#m;
	#h;
	#g;
	constructor() {
		this.#e = !1, this.#t = [], this.#n = [], this.#r = !1, this.#i = [], this.#a = [], this.#o = !1, this.#s = [], this.#c = [], this.#l = [], this.#u = [], this.#d = !1, this.#f = [], this.#p = [], this.#m = [], this.#h = [], this.#g = { toCanonicalUnits: !0 };
	}
	get hasNum() {
		return this.#e;
	}
	set hasNum(e) {
		this.#e = !!e;
	}
	get numSum() {
		return this.#t;
	}
	get numMul() {
		return this.#n;
	}
	get hasPct() {
		return this.#r;
	}
	set hasPct(e) {
		this.#r = !!e;
	}
	get pctSum() {
		return this.#i;
	}
	get pctMul() {
		return this.#a;
	}
	get hasDim() {
		return this.#o;
	}
	set hasDim(e) {
		this.#o = !!e;
	}
	get dimSum() {
		return this.#s;
	}
	get dimSub() {
		return this.#c;
	}
	get dimMul() {
		return this.#l;
	}
	get dimDiv() {
		return this.#u;
	}
	get hasEtc() {
		return this.#d;
	}
	set hasEtc(e) {
		this.#d = !!e;
	}
	get etcSum() {
		return this.#f;
	}
	get etcSub() {
		return this.#p;
	}
	get etcMul() {
		return this.#m;
	}
	get etcDiv() {
		return this.#h;
	}
	clear() {
		this.#e = !1, this.#t.length = 0, this.#n.length = 0, this.#r = !1, this.#i.length = 0, this.#a.length = 0, this.#o = !1, this.#s.length = 0, this.#c.length = 0, this.#l.length = 0, this.#u.length = 0, this.#d = !1, this.#f.length = 0, this.#p.length = 0, this.#m.length = 0, this.#h.length = 0;
	}
	sort(e = []) {
		let t = [...e];
		return t.length > 1 && t.sort((e, t) => {
			let n = tn(e), r = tn(t);
			if (n && r && n.unit && r.unit) {
				let { num: e, unit: t } = n, { num: i, unit: a } = r;
				return t === a ? e === i ? 0 : e > i ? 1 : -1 : t > a ? 1 : -1;
			}
			return e === t ? 0 : e > t ? 1 : -1;
		}), t;
	}
	multiply() {
		let e = [], t;
		if (this.#e) {
			t = 1;
			for (let e of this.#n) if (t *= e, t === 0 || !Number.isFinite(t) || Number.isNaN(t)) break;
			this.#r || this.#o || this.hasEtc || (Number.isFinite(t) && (t = L(t, 16)), e.push(t));
		}
		if (this.#r) {
			typeof t != "number" && (t = 1);
			for (let e of this.#a) if (t *= e, t === 0 || !Number.isFinite(t) || Number.isNaN(t)) break;
			Number.isFinite(t) && (t = `${L(t, 16)}%`), !this.#o && !this.hasEtc && e.push(t);
		}
		if (this.#o) {
			let n = "", r = "", i = "";
			this.#l.length && (this.#l.length === 1 ? [r] = this.#l : r = `${this.sort(this.#l).join(" * ")}`), this.#u.length && (this.#u.length === 1 ? [i] = this.#u : i = `${this.sort(this.#u).join(" * ")}`), Number.isFinite(t) ? (n = r ? i ? i.includes("*") ? u(`calc(${t} * ${r} / (${i}))`, this.#g) : u(`calc(${t} * ${r} / ${i})`, this.#g) : u(`calc(${t} * ${r})`, this.#g) : i.includes("*") ? u(`calc(${t} / (${i}))`, this.#g) : u(`calc(${t} / ${i})`, this.#g), e.push(n.replace(/^calc/, ""))) : (!e.length && t !== void 0 && e.push(t), r ? (n = i ? i.includes("*") ? u(`calc(${r} / (${i}))`, this.#g) : u(`calc(${r} / ${i})`, this.#g) : u(`calc(${r})`, this.#g), e.length ? e.push("*", n.replace(/^calc/, "")) : e.push(n.replace(/^calc/, ""))) : (n = u(`calc(${i})`, this.#g), e.length ? e.push("/", n.replace(/^calc/, "")) : e.push("1", "/", n.replace(/^calc/, ""))));
		}
		if (this.#d) {
			if (this.#m.length) {
				!e.length && t !== void 0 && e.push(t);
				let n = this.sort(this.#m).join(" * ");
				e.length ? e.push(`* ${n}`) : e.push(`${n}`);
			}
			if (this.#h.length) {
				let t = this.sort(this.#h).join(" * ");
				t.includes("*") ? e.length ? e.push(`/ (${t})`) : e.push(`1 / (${t})`) : e.length ? e.push(`/ ${t}`) : e.push(`1 / ${t}`);
			}
		}
		return e.length ? e.join(" ") : "";
	}
	sum() {
		let e = [];
		if (this.#e) {
			let t = 0;
			for (let e of this.#t) if (t += e, !Number.isFinite(t) || Number.isNaN(t)) break;
			e.push(t);
		}
		if (this.#r) {
			let t = 0;
			for (let e of this.#i) if (t += e, !Number.isFinite(t)) break;
			Number.isFinite(t) && (t = `${t}%`), e.length ? e.push(`+ ${t}`) : e.push(t);
		}
		if (this.#o) {
			let t, n, r;
			this.#s.length && (n = this.sort(this.#s).join(" + ")), this.#c.length && (r = this.sort(this.#c).join(" + ")), t = n ? r ? r.includes("-") ? u(`calc(${n} - (${r}))`, this.#g) : u(`calc(${n} - ${r})`, this.#g) : u(`calc(${n})`, this.#g) : u(`calc(-1 * (${r}))`, this.#g), e.length ? e.push("+", t.replace(/^calc/, "")) : e.push(t.replace(/^calc/, ""));
		}
		if (this.#d) {
			if (this.#f.length) {
				let t = this.sort(this.#f).map((e) => {
					let t;
					return t = c({ css: e }).some(([e, t]) => e === Rt && Jt.has(t)) && !e.startsWith("(") && !e.endsWith(")") ? `(${e})` : e, t;
				}).join(" + ");
				e.length ? this.#f.length > 1 ? e.push(`+ (${t})`) : e.push(`+ ${t}`) : e.push(`${t}`);
			}
			if (this.#p.length) {
				let t = this.sort(this.#p).map((e) => {
					let t;
					return t = c({ css: e }).some(([e, t]) => e === Rt && Jt.has(t)) && !e.startsWith("(") && !e.endsWith(")") ? `(${e})` : e, t;
				}).join(" + ");
				e.length ? this.#p.length > 1 ? e.push(`- (${t})`) : e.push(`- ${t}`) : this.#p.length > 1 ? e.push(`-1 * (${t})`) : e.push(`-1 * ${t}`);
			}
		}
		return e.length ? e.join(" ") : "";
	}
}, rn = (e = [], t = !1) => {
	if (e.length < 3) throw Error(`Unexpected array length ${e.length}.`);
	let n = e.shift();
	if (!v(n) || !n.endsWith("(")) throw Error(`Unexpected token ${n}.`);
	let r = e.pop();
	if (r !== ")") throw Error(`Unexpected token ${r}.`);
	if (e.length === 1) {
		let [t] = e;
		if (!y(t)) throw Error(`Unexpected token ${t}.`);
		return `${n}${t}${r}`;
	}
	let i = [], a = new nn(), o = "", s = !1;
	for (let t of e) {
		if (!y(t)) throw Error(`Unexpected token ${t}.`);
		if (t === "*" || t === "/") o = t;
		else if (t === "+" || t === "-") {
			let e = a.multiply();
			e && i.push(e, t), s = !0, a.clear(), o = "";
		} else {
			let e = Number(t), n = `${t}`, r = tn(n), i = r && r.unit !== "" && r.unit !== "%";
			switch (o) {
				case "/":
					Number.isFinite(e) ? (a.hasNum = !0, a.numMul.push(1 / e)) : r && r.unit === "%" ? (a.hasPct = !0, a.pctMul.push(1e4 / r.num)) : i ? (a.hasDim = !0, a.dimDiv.push(n)) : (a.hasEtc = !0, a.etcDiv.push(n));
					break;
				default: Number.isFinite(e) ? (a.hasNum = !0, a.numMul.push(e)) : r && r.unit === "%" ? (a.hasPct = !0, a.pctMul.push(r.num)) : i ? (a.hasDim = !0, a.dimMul.push(n)) : (a.hasEtc = !0, a.etcMul.push(n));
			}
		}
	}
	let c = a.multiply();
	c && i.push(c);
	let l = "";
	if (t && s) {
		let e = [];
		a.clear(), o = "";
		for (let e of i) if (e === "+" || e === "-") o = e;
		else {
			let t = Number(e), n = `${e}`, r = tn(n), i = r && r.unit !== "" && r.unit !== "%";
			switch (o) {
				case "-":
					Number.isFinite(t) ? (a.hasNum = !0, a.numSum.push(-1 * t)) : r && r.unit === "%" ? (a.hasPct = !0, a.pctSum.push(-1 * r.num)) : i ? (a.hasDim = !0, a.dimSub.push(n)) : (a.hasEtc = !0, a.etcSub.push(n));
					break;
				default: Number.isFinite(t) ? (a.hasNum = !0, a.numSum.push(t)) : r && r.unit === "%" ? (a.hasPct = !0, a.pctSum.push(r.num)) : i ? (a.hasDim = !0, a.dimSum.push(n)) : (a.hasEtc = !0, a.etcSum.push(n));
			}
		}
		let t = a.sum();
		e.push(t), a.clear(), o = "", l = e.join(" ").replace(/\+\s-/g, "- ");
	} else l = i.join(" ").replace(/\+\s-/g, "- ");
	return l.startsWith("(") && l.endsWith(")") && l.lastIndexOf("(") === 0 && l.indexOf(")") === l.length - 1 && (l = l.substring(1, l.length - 1)), `${n}${l}${r}`;
}, an = (e) => {
	let t = c({ css: e }), n = [], r = [], i = "", a = 0;
	for (let e of t) {
		let [t, o] = e;
		if (t === Wt || t === Vt ? a++ : t === It && a--, a === 0 && (o === "+" || o === "-")) {
			let e = r.join("").trim();
			e && n.push((i === "-" ? "-" : "") + e), r = [], i = o;
		} else r.push(o);
	}
	let o = r.join("").trim();
	if (o && n.push((i === "-" ? "-" : "") + o), n.length === 0) return e;
	n.sort((e, t) => {
		let n = tn(e), r = tn(t);
		return n && r ? n.unit === r.unit ? n.num - r.num : n.unit > r.unit ? 1 : -1 : e === t ? 0 : e > t ? 1 : -1;
	});
	let [s, ...l] = n, u = s;
	for (let e of l) e.startsWith("-") ? u += ` - ${e.substring(1)}` : u += ` + ${e}`;
	return u;
}, on = (e, t) => {
	let n = e[0], r = v(n) && Zt.test(n), i = [];
	for (let t of e) Array.isArray(t) ? i.push(on(t, r)) : i.push(t);
	let a = i.includes(","), o = i[0] ?? "", s = v(o) && Zt.test(o);
	if (a && s && i.at(-1) === ")") {
		let e = i.shift(), t = i.pop(), n = [], r = [];
		for (let e of i) e === "," ? (n.push(r), r = []) : r.push(e);
		return r.length && n.push(r), `${e}${n.map((e) => {
			if (e.length >= 3) {
				let t = rn([
					ge,
					...e,
					")"
				], !0);
				return an(t.substring(5, t.length - 1));
			}
			if (e.length === 1) {
				let t = e[0];
				if (v(t) && t.startsWith("calc(") && t.endsWith(")")) return an(t.substring(5, t.length - 1));
			}
			return e.join("");
		}).join(", ")}${t}`;
	}
	if (t) {
		if (!a) {
			if (i.length >= 3) return rn(i, !0);
			let e = i[0] ?? "";
			return e.endsWith("(") ? i.join("") : /^[a-z-]+\(/.test(e) ? e : `calc(${e})`;
		}
		return i.join("").replace(/,\s*/g, ", ");
	}
	if (i.length >= 3 && !a) {
		let e = rn(i, !1);
		return $t.test(e) && (e = u(e, { toCanonicalUnits: !0 })), e;
	}
	return i.join("").replace(/,\s*/g, ", ");
}, sn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let { format: n = "" } = t;
	if (!$t.test(e) || n !== "specifiedValue") return e;
	let r = e.trim(), i = _({
		namespace: Kt,
		name: "serializeCalc",
		value: r
	}, t), a = h(i);
	if (a !== !1) return a.item;
	let o = c({ css: r }).filter((e) => {
		let [t] = e;
		return t !== Gt && t !== Lt && t !== Bt;
	}), s = [[]];
	for (let e of o) {
		let [t, n] = e;
		if (t === Wt || t === Vt) {
			let e = [n];
			s.at(-1).push(e), s.push(e);
		} else t === It ? s.length > 1 ? s.pop().push(n) : s[0].push(n) : s.at(-1).push(n);
	}
	let l = "", u = s[0];
	return l = u.length === 1 && Array.isArray(u[0]) ? on(u[0], !0) : on(u, !0), m(i, l), l;
}, cn = (e, t = {}) => {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	let [, , , , n = {}] = e, { unit: r, value: i } = n;
	if (r === "px") return `${i}${r}`;
	let a = nt(Number(i), r, t);
	return Number.isFinite(a) ? `${L(a, 16)}px` : null;
}, ln = (e, t = {}) => {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	let { format: n = "" } = t, r = /* @__PURE__ */ new Set(), i = 0, a = [];
	for (let o of e) {
		if (!Array.isArray(o)) throw TypeError(`${o} is not an array.`);
		let [e = "", s = ""] = o;
		switch (e) {
			case zt:
				if (n === "specifiedValue" && !r.has(i)) a.push(s);
				else {
					let e = cn(o, t);
					v(e) ? a.push(e) : a.push(s);
				}
				break;
			case Vt:
			case Wt:
				a.push(s), i++, Zt.test(s) && r.add(i);
				break;
			case It:
				a.length && a.at(-1) === " " ? a.splice(-1, 1, s) : a.push(s), r.has(i) && r.delete(i), i--;
				break;
			case Gt:
				if (a.length) {
					let e = a.at(-1);
					v(e) && !e.endsWith("(") && e !== " " && a.push(s);
				}
				break;
			default: e !== Lt && e !== Bt && a.push(s);
		}
	}
	return a;
}, U = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let n = { ...t }, r = $e(n);
	if (e.length > r) return "";
	let { format: i } = n;
	if (Qt.test(e)) {
		if (i === "specifiedValue") return e;
		{
			let t = fn(e, n);
			return v(t) ? t : "";
		}
	}
	if (!Yt.test(e)) return e;
	let a = e.trim(), o = _({
		namespace: Kt,
		name: "cssCalc",
		value: a
	}, n), s = h(o);
	if (s !== !1) return s.item;
	let l = ln(c({ css: a }), n), d = u(l.join(""), { toCanonicalUnits: !0 });
	if ($t.test(e)) {
		let e = tn(d);
		e && e.unit !== "" && (d = `${L(Number(e.num), 16)}${e.unit}`), d && !$t.test(d) && i === "specifiedValue" && (d = `calc(${d})`);
	}
	if (i === "specifiedValue") {
		if (en.test(d) && !d.includes("NaN")) d = sn(d, n);
		else if (Xt.test(d)) {
			let [, e] = d.match(Xt);
			d = `calc(${L(Number(e), 16)})`;
		}
	}
	return m(o, d), d;
};
function un(e, t = {}) {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	let { customProperty: n = {} } = t, r = [];
	for (; e.length;) {
		let i = e.shift();
		if (!i) break;
		if (!Array.isArray(i)) throw TypeError(`${i} is not an array.`);
		let [a, o] = i;
		if (a === It) break;
		if (o === "var(") {
			let [, n] = un(e, t);
			n && r.push(n);
		} else if (o && a === Ht) {
			if (o.startsWith("--")) {
				let e;
				if (n[o]) {
					let t = n[o];
					e = typeof t == "function" ? t(o) : t;
				} else typeof n.callback == "function" && (e = n.callback(o));
				e && r.push(e);
			} else r.push(o);
		}
	}
	let i = !1;
	r.length > 1 && (i = Qr(r.at(-1)));
	let a = "";
	for (let e of r) {
		if (e = e.trim(), Qt.test(e)) {
			let n = fn(e, t);
			v(n) && (!i || Qr(n)) && (a = n);
		} else Yt.test(e) ? (e = U(e, t), (!i || Qr(e)) && (a = e)) : e && !qt.has(e) && (!i || Qr(e)) && (a = e);
		if (a) break;
	}
	return [e, a];
}
function dn(e, t = {}) {
	let n = [];
	for (; e.length;) {
		let r = e.shift();
		if (!r) break;
		let [i = "", a = ""] = r;
		if (a === "var(") {
			let [, r] = un(e, t);
			if (!r) return null;
			n.push(r);
		} else switch (i) {
			case It:
				n.length && n.at(-1) === " " ? n[n.length - 1] = a : n.push(a);
				break;
			case Gt:
				if (n.length) {
					let e = n.at(-1);
					v(e) && !e.endsWith("(") && e !== " " && n.push(a);
				}
				break;
			default: i !== Lt && i !== Bt && n.push(a);
		}
	}
	return n;
}
function fn(e, t = {}) {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let n = $e(t);
	if (e.length > n) return null;
	if (!Qt.test(e) || t.format === "specifiedValue") return e;
	let r = e.trim(), i = _({
		namespace: Kt,
		name: "resolveVar",
		value: r
	}, t), a = h(i);
	if (a !== !1) return a.item;
	let o = dn(c({ css: r }), t);
	if (Array.isArray(o)) {
		let e = o.join("");
		return e.length > n ? (m(i, null), null) : (Yt.test(e) && (e = U(e, t)), m(i, e), e);
	}
	return m(i, null), null;
}
var pn = (e, t = {}) => {
	let n = fn(e, { ...t });
	return v(n) ? n : "";
}, mn = {
	aliceblue: [
		240,
		248,
		255
	],
	antiquewhite: [
		250,
		235,
		215
	],
	aqua: [
		0,
		255,
		255
	],
	aquamarine: [
		127,
		255,
		212
	],
	azure: [
		240,
		255,
		255
	],
	beige: [
		245,
		245,
		220
	],
	bisque: [
		255,
		228,
		196
	],
	black: [
		0,
		0,
		0
	],
	blanchedalmond: [
		255,
		235,
		205
	],
	blue: [
		0,
		0,
		255
	],
	blueviolet: [
		138,
		43,
		226
	],
	brown: [
		165,
		42,
		42
	],
	burlywood: [
		222,
		184,
		135
	],
	cadetblue: [
		95,
		158,
		160
	],
	chartreuse: [
		127,
		255,
		0
	],
	chocolate: [
		210,
		105,
		30
	],
	coral: [
		255,
		127,
		80
	],
	cornflowerblue: [
		100,
		149,
		237
	],
	cornsilk: [
		255,
		248,
		220
	],
	crimson: [
		220,
		20,
		60
	],
	cyan: [
		0,
		255,
		255
	],
	darkblue: [
		0,
		0,
		139
	],
	darkcyan: [
		0,
		139,
		139
	],
	darkgoldenrod: [
		184,
		134,
		11
	],
	darkgray: [
		169,
		169,
		169
	],
	darkgreen: [
		0,
		100,
		0
	],
	darkgrey: [
		169,
		169,
		169
	],
	darkkhaki: [
		189,
		183,
		107
	],
	darkmagenta: [
		139,
		0,
		139
	],
	darkolivegreen: [
		85,
		107,
		47
	],
	darkorange: [
		255,
		140,
		0
	],
	darkorchid: [
		153,
		50,
		204
	],
	darkred: [
		139,
		0,
		0
	],
	darksalmon: [
		233,
		150,
		122
	],
	darkseagreen: [
		143,
		188,
		143
	],
	darkslateblue: [
		72,
		61,
		139
	],
	darkslategray: [
		47,
		79,
		79
	],
	darkslategrey: [
		47,
		79,
		79
	],
	darkturquoise: [
		0,
		206,
		209
	],
	darkviolet: [
		148,
		0,
		211
	],
	deeppink: [
		255,
		20,
		147
	],
	deepskyblue: [
		0,
		191,
		255
	],
	dimgray: [
		105,
		105,
		105
	],
	dimgrey: [
		105,
		105,
		105
	],
	dodgerblue: [
		30,
		144,
		255
	],
	firebrick: [
		178,
		34,
		34
	],
	floralwhite: [
		255,
		250,
		240
	],
	forestgreen: [
		34,
		139,
		34
	],
	fuchsia: [
		255,
		0,
		255
	],
	gainsboro: [
		220,
		220,
		220
	],
	ghostwhite: [
		248,
		248,
		255
	],
	gold: [
		255,
		215,
		0
	],
	goldenrod: [
		218,
		165,
		32
	],
	gray: [
		128,
		128,
		128
	],
	green: [
		0,
		128,
		0
	],
	greenyellow: [
		173,
		255,
		47
	],
	grey: [
		128,
		128,
		128
	],
	honeydew: [
		240,
		255,
		240
	],
	hotpink: [
		255,
		105,
		180
	],
	indianred: [
		205,
		92,
		92
	],
	indigo: [
		75,
		0,
		130
	],
	ivory: [
		255,
		255,
		240
	],
	khaki: [
		240,
		230,
		140
	],
	lavender: [
		230,
		230,
		250
	],
	lavenderblush: [
		255,
		240,
		245
	],
	lawngreen: [
		124,
		252,
		0
	],
	lemonchiffon: [
		255,
		250,
		205
	],
	lightblue: [
		173,
		216,
		230
	],
	lightcoral: [
		240,
		128,
		128
	],
	lightcyan: [
		224,
		255,
		255
	],
	lightgoldenrodyellow: [
		250,
		250,
		210
	],
	lightgray: [
		211,
		211,
		211
	],
	lightgreen: [
		144,
		238,
		144
	],
	lightgrey: [
		211,
		211,
		211
	],
	lightpink: [
		255,
		182,
		193
	],
	lightsalmon: [
		255,
		160,
		122
	],
	lightseagreen: [
		32,
		178,
		170
	],
	lightskyblue: [
		135,
		206,
		250
	],
	lightslategray: [
		119,
		136,
		153
	],
	lightslategrey: [
		119,
		136,
		153
	],
	lightsteelblue: [
		176,
		196,
		222
	],
	lightyellow: [
		255,
		255,
		224
	],
	lime: [
		0,
		255,
		0
	],
	limegreen: [
		50,
		205,
		50
	],
	linen: [
		250,
		240,
		230
	],
	magenta: [
		255,
		0,
		255
	],
	maroon: [
		128,
		0,
		0
	],
	mediumaquamarine: [
		102,
		205,
		170
	],
	mediumblue: [
		0,
		0,
		205
	],
	mediumorchid: [
		186,
		85,
		211
	],
	mediumpurple: [
		147,
		112,
		219
	],
	mediumseagreen: [
		60,
		179,
		113
	],
	mediumslateblue: [
		123,
		104,
		238
	],
	mediumspringgreen: [
		0,
		250,
		154
	],
	mediumturquoise: [
		72,
		209,
		204
	],
	mediumvioletred: [
		199,
		21,
		133
	],
	midnightblue: [
		25,
		25,
		112
	],
	mintcream: [
		245,
		255,
		250
	],
	mistyrose: [
		255,
		228,
		225
	],
	moccasin: [
		255,
		228,
		181
	],
	navajowhite: [
		255,
		222,
		173
	],
	navy: [
		0,
		0,
		128
	],
	oldlace: [
		253,
		245,
		230
	],
	olive: [
		128,
		128,
		0
	],
	olivedrab: [
		107,
		142,
		35
	],
	orange: [
		255,
		165,
		0
	],
	orangered: [
		255,
		69,
		0
	],
	orchid: [
		218,
		112,
		214
	],
	palegoldenrod: [
		238,
		232,
		170
	],
	palegreen: [
		152,
		251,
		152
	],
	paleturquoise: [
		175,
		238,
		238
	],
	palevioletred: [
		219,
		112,
		147
	],
	papayawhip: [
		255,
		239,
		213
	],
	peachpuff: [
		255,
		218,
		185
	],
	peru: [
		205,
		133,
		63
	],
	pink: [
		255,
		192,
		203
	],
	plum: [
		221,
		160,
		221
	],
	powderblue: [
		176,
		224,
		230
	],
	purple: [
		128,
		0,
		128
	],
	rebeccapurple: [
		102,
		51,
		153
	],
	red: [
		255,
		0,
		0
	],
	rosybrown: [
		188,
		143,
		143
	],
	royalblue: [
		65,
		105,
		225
	],
	saddlebrown: [
		139,
		69,
		19
	],
	salmon: [
		250,
		128,
		114
	],
	sandybrown: [
		244,
		164,
		96
	],
	seagreen: [
		46,
		139,
		87
	],
	seashell: [
		255,
		245,
		238
	],
	sienna: [
		160,
		82,
		45
	],
	silver: [
		192,
		192,
		192
	],
	skyblue: [
		135,
		206,
		235
	],
	slateblue: [
		106,
		90,
		205
	],
	slategray: [
		112,
		128,
		144
	],
	slategrey: [
		112,
		128,
		144
	],
	snow: [
		255,
		250,
		250
	],
	springgreen: [
		0,
		255,
		127
	],
	steelblue: [
		70,
		130,
		180
	],
	tan: [
		210,
		180,
		140
	],
	teal: [
		0,
		128,
		128
	],
	thistle: [
		216,
		191,
		216
	],
	tomato: [
		255,
		99,
		71
	],
	turquoise: [
		64,
		224,
		208
	],
	violet: [
		238,
		130,
		238
	],
	wheat: [
		245,
		222,
		179
	],
	white: [
		255,
		255,
		255
	],
	whitesmoke: [
		245,
		245,
		245
	],
	yellow: [
		255,
		255,
		0
	],
	yellowgreen: [
		154,
		205,
		50
	]
}, hn = RegExp(`^(?:${we})$`), gn = /^currentColor$/i, W = /^(?:specifi|comput)edValue$/, _n = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.toLowerCase().trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "rgb") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : Math.min(Math.max(L(o * 255, 16), 0), 255), f = Number.isNaN(s) ? 0 : Math.min(Math.max(L(s * 255, 16), 0), 255), p = Number.isNaN(c) ? 0 : Math.min(Math.max(L(c * 255, 16), 0), 255), m;
	return m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0, [
		"rgb",
		d,
		f,
		p,
		r === "mixValue" && Number.isNaN(l) && u ? O : m
	];
}, vn = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "hsl") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : o, f = Number.isNaN(s) ? 0 : Math.min(Math.max(s, 0), 100), p = Number.isNaN(c) ? 0 : Math.min(Math.max(c, 0), 100), m;
	m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0;
	let h = Number.isNaN(l) && u;
	if (r === "hsl") return [
		r,
		Number.isNaN(o) ? O : d,
		Number.isNaN(s) ? O : f,
		Number.isNaN(c) ? O : p,
		h ? O : m
	];
	d = d / 360 * 12, p /= 100;
	let g = f / 100 * Math.min(p, 1 - p), _ = d % 12, y = (8 + d) % 12, b = (4 + d) % 12, x = p - g * Math.max(-1, Math.min(_ - 3, 9 - _, 1)), S = p - g * Math.max(-1, Math.min(y - 3, 9 - y, 1)), C = p - g * Math.max(-1, Math.min(b - 3, 9 - b, 1));
	return [
		"rgb",
		Math.min(Math.max(L(x * 255, 16), 0), 255),
		Math.min(Math.max(L(S * 255, 16), 0), 255),
		Math.min(Math.max(L(C * 255, 16), 0), 255),
		m
	];
}, yn = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "hwb") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : o, f = Number.isNaN(s) ? 0 : Math.min(Math.max(s, 0), 100) / 100, p = Number.isNaN(c) ? 0 : Math.min(Math.max(c, 0), 100) / 100, m;
	m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0;
	let h = Number.isNaN(l) && u;
	if (r === "hwb") return [
		r,
		Number.isNaN(o) ? O : d,
		Number.isNaN(s) ? O : f * 100,
		Number.isNaN(c) ? O : p * 100,
		h ? O : m
	];
	if (f + p >= 1) {
		let e = L(f / (f + p) * 255, 16);
		return [
			"rgb",
			e,
			e,
			e,
			m
		];
	}
	let g = (1 - f - p) / 255, [, _, y, b] = vn(`hsl(${d} 100 50)`);
	return _ = L((_ * g + f) * 255, 16), y = L((y * g + f) * 255, 16), b = L((b * g + f) * 255, 16), [
		"rgb",
		Math.min(Math.max(_, 0), 255),
		Math.min(Math.max(y, 0), 255),
		Math.min(Math.max(b, 0), 255),
		m
	];
}, bn = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "lab") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : Math.max(o, 0), f = Number.isNaN(s) ? 0 : s, p = Number.isNaN(c) ? 0 : c, m;
	m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0;
	let h = Number.isNaN(l) && u;
	if (W.test(r)) return [
		"lab",
		Number.isNaN(o) ? O : L(d, 16),
		Number.isNaN(s) ? O : L(f, 16),
		Number.isNaN(c) ? O : L(p, 16),
		h ? O : m
	];
	let g = (d + 16) / 116, _ = f / 500 + g, y = g - p / 200, b = g ** 3, x = _ ** 3, S = y ** 3, [C, w, T] = [
		x > .008856451679035631 ? x : (_ * 116 - 16) / Ae,
		d > 8 ? b : d / Ae,
		S > .008856451679035631 ? S : (y * 116 - 16) / Ae
	].map((e, t) => e * ct[t]);
	return [
		"xyz-d50",
		L(C, 16),
		L(w, 16),
		L(T, 16),
		m
	];
}, xn = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "lch") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : Math.max(o, 0), f = Number.isNaN(s) ? 0 : Math.max(s, 0), p = Number.isNaN(c) ? 0 : c, m;
	m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0;
	let h = Number.isNaN(l) && u;
	if (W.test(r)) return [
		"lch",
		Number.isNaN(o) ? O : L(d, 16),
		Number.isNaN(s) ? O : L(f, 16),
		Number.isNaN(c) ? O : L(p, 16),
		h ? O : m
	];
	let [, g, _, y] = bn(`lab(${d} ${f * Math.cos(p * Math.PI / 180)} ${f * Math.sin(p * Math.PI / 180)})`);
	return [
		"xyz-d50",
		L(g, 16),
		L(_, 16),
		L(y, 16),
		m
	];
}, Sn = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "oklab") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : Math.max(o, 0), f = Number.isNaN(s) ? 0 : s, p = Number.isNaN(c) ? 0 : c, m;
	m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0;
	let h = Number.isNaN(l) && u;
	if (W.test(r)) return [
		"oklab",
		Number.isNaN(o) ? O : L(d, 16),
		Number.isNaN(s) ? O : L(f, 16),
		Number.isNaN(c) ? O : L(p, 16),
		h ? O : m
	];
	let [g, _, y] = H(pt, H(mt, [
		d,
		f,
		p
	]).map((e) => e ** 3), !0);
	return [
		"xyz-d65",
		L(g, 16),
		L(_, 16),
		L(y, 16),
		m
	];
}, Cn = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { format: r = "", nullable: i = !1 } = n, a = at(t);
	if (!a || a.colorNotation !== "oklch") {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let [o, s, c] = a.channels, l = a.alpha, u = a.syntaxFlags instanceof Set && a.syntaxFlags.has(e.HasNoneKeywords), d = Number.isNaN(o) ? 0 : Math.max(o, 0), f = Number.isNaN(s) ? 0 : Math.max(s, 0), p = Number.isNaN(c) ? 0 : c, m;
	m = typeof l == "number" && !Number.isNaN(l) ? R(String(l)) : 0;
	let h = Number.isNaN(l) && u;
	if (W.test(r)) return [
		"oklch",
		Number.isNaN(o) ? O : L(d, 16),
		Number.isNaN(s) ? O : L(f, 16),
		Number.isNaN(c) ? O : L(p, 16),
		h ? O : m
	];
	let [g, _, y] = H(pt, H(mt, [
		d,
		f * Math.cos(p * Math.PI / 180),
		f * Math.sin(p * Math.PI / 180)
	]).map((e) => e ** 3), !0);
	return [
		"xyz-d65",
		L(g, 16),
		L(_, 16),
		L(y, 16),
		m
	];
}, G = (t, n = {}) => {
	if (!v(t)) throw TypeError(`${t} is not a string.`);
	t = t.trim();
	let { colorSpace: r = "", d50: i = !1, format: a = "", nullable: o = !1 } = n, s = at(t);
	if (!s) {
		let e = z(a, o);
		return e === null ? null : (v(e), e);
	}
	let c = s.colorNotation, [l, u, d] = s.channels, f = s.alpha, p = s.syntaxFlags instanceof Set && s.syntaxFlags.has(e.HasNoneKeywords), m = Number.isNaN(l) ? 0 : l, h = Number.isNaN(u) ? 0 : u, g = Number.isNaN(d) ? 0 : d, _;
	_ = typeof f == "number" && !Number.isNaN(f) ? R(String(f)) : 0;
	let y = Number.isNaN(f) && p;
	if (W.test(a) || a === "mixValue" && c === r) return [
		c,
		Number.isNaN(l) ? O : L(m, 16),
		Number.isNaN(u) ? O : L(h, 16),
		Number.isNaN(d) ? O : L(g, 16),
		y ? O : _
	];
	let b = 0, x = 0, S = 0;
	if (c === "srgb-linear") [b, x, S] = H(ut, [
		m,
		h,
		g
	]), i && ([b, x, S] = H(B, [
		b,
		x,
		S
	], !0));
	else if (c === "display-p3") {
		let e = xt([
			m * 255,
			h * 255,
			g * 255
		]);
		[b, x, S] = H(gt, e), i && ([b, x, S] = H(B, [
			b,
			x,
			S
		], !0));
	} else if (c === "rec2020") {
		let e = 1.09929682680944, t = .45, n = [
			m,
			h,
			g
		].map((n) => {
			let r;
			return r = n < .018053968510807 * t * 10 ? n / (t * 10) : ((n + e - 1) / e) ** (1 / t), r;
		});
		[b, x, S] = H(_t, n), i && ([b, x, S] = H(B, [
			b,
			x,
			S
		], !0));
	} else if (c === "a98-rgb") {
		let e = [
			m,
			h,
			g
		].map((e) => e ** 2.19921875);
		[b, x, S] = H(vt, e), i && ([b, x, S] = H(B, [
			b,
			x,
			S
		], !0));
	} else if (c === "prophoto-rgb") {
		let e = [
			m,
			h,
			g
		].map((e) => {
			let t;
			return t = e > 1 / 32 ? e ** 1.8 : e / 16, t;
		});
		[b, x, S] = H(yt, e), i || ([b, x, S] = H(lt, [
			b,
			x,
			S
		], !0));
	} else /^xyz(?:-d(?:50|65))?$/.test(c) ? ([b, x, S] = [
		m,
		h,
		g
	], c === "xyz-d50" ? i || ([b, x, S] = H(lt, [
		b,
		x,
		S
	])) : i && ([b, x, S] = H(B, [
		b,
		x,
		S
	], !0))) : ([b, x, S] = St([
		m * 255,
		h * 255,
		g * 255
	]), i && ([b, x, S] = H(B, [
		b,
		x,
		S
	], !0)));
	return [
		i ? "xyz-d50" : "xyz-d65",
		L(b, 16),
		L(x, 16),
		L(S, 16),
		a === "mixValue" && y ? O : _
	];
}, K = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.toLowerCase().trim();
	let { d50: n = !1, format: r = "", nullable: i = !1 } = t;
	if (!hn.test(e)) {
		let e = z(r, i);
		return e === null ? null : (v(e), e);
	}
	let a = 0, o = 0, s = 0, c = 0;
	if (gn.test(e)) {
		if (r === "computedValue") return [
			"rgb",
			0,
			0,
			0,
			0
		];
		if (r === "specifiedValue") return e;
	} else if (/^[a-z]+$/.test(e)) {
		if (Object.hasOwn(mn, e)) {
			if (r === "specifiedValue") return e;
			let [t, i, l] = mn[e];
			if (c = 1, r === "computedValue") return [
				"rgb",
				t,
				i,
				l,
				c
			];
			[a, o, s] = St([
				t,
				i,
				l
			], !0), n && ([a, o, s] = H(B, [
				a,
				o,
				s
			], !0));
		} else switch (r) {
			case N: return i && e !== "transparent" ? null : [
				"rgb",
				0,
				0,
				0,
				0
			];
			case Oe: return e === "transparent" ? e : "";
			case P: return e === "transparent" ? [
				"rgb",
				0,
				0,
				0,
				0
			] : null;
		}
	} else if (e[0] === "#") {
		if (W.test(r)) return ["rgb", ...Nt(e)];
		[a, o, s, c] = Ft(e), n && ([a, o, s] = H(B, [
			a,
			o,
			s
		], !0));
	} else if (e.startsWith("lab")) {
		if (W.test(r)) return bn(e, t);
		[, a, o, s, c] = bn(e), n || ([a, o, s] = H(lt, [
			a,
			o,
			s
		], !0));
	} else if (e.startsWith("lch")) {
		if (W.test(r)) return xn(e, t);
		[, a, o, s, c] = xn(e), n || ([a, o, s] = H(lt, [
			a,
			o,
			s
		], !0));
	} else if (e.startsWith("oklab")) {
		if (W.test(r)) return Sn(e, t);
		[, a, o, s, c] = Sn(e), n && ([a, o, s] = H(B, [
			a,
			o,
			s
		], !0));
	} else if (e.startsWith("oklch")) {
		if (W.test(r)) return Cn(e, t);
		[, a, o, s, c] = Cn(e), n && ([a, o, s] = H(B, [
			a,
			o,
			s
		], !0));
	} else {
		let i, l, u;
		if (e.startsWith("hsl") ? [, i, l, u, c] = vn(e) : e.startsWith("hwb") ? [, i, l, u, c] = yn(e) : [, i, l, u, c] = _n(e, t), W.test(r)) return [
			"rgb",
			Math.round(i),
			Math.round(l),
			Math.round(u),
			c
		];
		[a, o, s] = St([
			i,
			l,
			u
		]), n && ([a, o, s] = H(B, [
			a,
			o,
			s
		], !0));
	}
	return [
		n ? "xyz-d50" : "xyz-d65",
		L(a, 16),
		L(o, 16),
		L(s, 16),
		c
	];
}, wn = RegExp(`^color\\(\\s*(${_e})\\s*\\)$`), Tn = RegExp(`^hsla?\\(\\s*(${be}|${xe})\\s*\\)$`), En = RegExp(`^hwb\\(\\s*(${be})\\s*\\)$`), Dn = RegExp(`^lab\\(\\s*(${Ce})\\s*\\)$`), On = RegExp(`^lch\\(\\s*(${Se})\\s*\\)$`), kn = RegExp(`^oklab\\(\\s*(${Ce})\\s*\\)$`), An = RegExp(`^oklch\\(\\s*(${Se})\\s*\\)$`), jn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { colorSpace: n = "", format: r = "" } = t, i = "", a, o, s, c, l, u, d;
	if (r === "mixValue") {
		let r;
		if (r = e.startsWith("color(") ? G(e, t) : K(e, t), r === null) return null;
		if ([i, l, u, d, c] = r, i === n) return [
			l,
			u,
			d,
			c
		];
		[a, o, s] = H(dt, [
			l,
			u,
			d
		], !0);
	} else if (e.startsWith("color(")) {
		let [, t] = e.match(wn), [n] = t.match(/[^\s,/]+/g);
		n === "srgb-linear" ? [, a, o, s, c] = J(e, { format: N }) : ([, l, u, d, c] = G(e), [a, o, s] = H(dt, [
			l,
			u,
			d
		], !0));
	} else [, l, u, d, c] = K(e), [a, o, s] = H(dt, [
		l,
		u,
		d
	], !0);
	return [
		Math.min(Math.max(a, 0), 1),
		Math.min(Math.max(o, 0), 1),
		Math.min(Math.max(s, 0), 1),
		c
	];
}, Mn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (n === "mixValue") {
		let n;
		if (n = e.startsWith("color(") ? J(e, t) : Zr(e, t), n === null) return null;
		[, r, i, a, o] = n;
	} else if (e.startsWith("color(")) {
		let [, t] = e.match(wn), [n] = t.match(/[^\s,/]+/g);
		n === "srgb" ? ([, r, i, a, o] = J(e, { format: N }), r *= 255, i *= 255, a *= 255) : [, r, i, a, o] = J(e);
	} else /^(?:ok)?l(?:ab|ch)/.test(e) ? ([r, i, a, o] = jn(e), [r, i, a] = Ct([
		r,
		i,
		a
	])) : [, r, i, a, o] = Zr(e, { format: N });
	return [
		r,
		i,
		a,
		o
	];
}, Nn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { d50: n = !1, format: r = "" } = t, i, a, o, s;
	if (r === "mixValue") {
		let n;
		if (n = e.startsWith("color(") ? G(e, t) : K(e, t), n === null) return null;
		[, i, a, o, s] = n;
	} else if (e.startsWith("color(")) {
		let [, r] = e.match(wn), [c] = r.match(/[^\s,/]+/g);
		n ? c === "xyz-d50" ? [, i, a, o, s] = J(e, { format: N }) : [, i, a, o, s] = G(e, t) : /^xyz(?:-d65)?$/.test(c) ? [, i, a, o, s] = J(e, { format: N }) : [, i, a, o, s] = G(e);
	} else [, i, a, o, s] = K(e, t);
	return [
		i,
		a,
		o,
		s
	];
}, Pn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (Tn.test(e)) return [, r, i, a, o] = vn(e, { format: "hsl" }), n === "hsl" ? [
		Math.round(r),
		Math.round(i),
		Math.round(a),
		o
	] : [
		r,
		i,
		a,
		o
	];
	let s, c, l;
	if (n === "mixValue") {
		let n;
		if (n = e.startsWith("color(") ? G(e, t) : K(e, t), n === null) return null;
		[, s, c, l, o] = n;
	} else e.startsWith("color(") ? [, s, c, l, o] = G(e) : [, s, c, l, o] = K(e);
	return [r, i, a] = Tt([
		s,
		c,
		l
	], !0), n === "hsl" ? [
		Math.round(r),
		Math.round(i),
		Math.round(a),
		o
	] : [
		n === "mixValue" && i === 0 ? O : r,
		i,
		a,
		o
	];
}, Fn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (En.test(e)) return [, r, i, a, o] = yn(e, { format: "hwb" }), n === "hwb" ? [
		Math.round(r),
		Math.round(i),
		Math.round(a),
		o
	] : [
		r,
		i,
		a,
		o
	];
	let s, c, l;
	if (n === "mixValue") {
		let n;
		if (n = e.startsWith("color(") ? G(e, t) : K(e, t), n === null) return null;
		[, s, c, l, o] = n;
	} else e.startsWith("color(") ? [, s, c, l, o] = G(e) : [, s, c, l, o] = K(e);
	return [r, i, a] = Et([
		s,
		c,
		l
	], !0), n === "hwb" ? [
		Math.round(r),
		Math.round(i),
		Math.round(a),
		o
	] : [
		n === "mixValue" && i + a >= 100 ? O : r,
		i,
		a,
		o
	];
}, In = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (Dn.test(e)) return [, r, i, a, o] = bn(e, { format: N }), [
		r,
		i,
		a,
		o
	];
	let s, c, l;
	if (n === "mixValue") {
		let n, r = {
			...t,
			d50: !0
		};
		if (n = e.startsWith("color(") ? G(e, r) : K(e, r), n === null) return null;
		[, s, c, l, o] = n;
	} else e.startsWith("color(") ? [, s, c, l, o] = G(e, { d50: !0 }) : [, s, c, l, o] = K(e, { d50: !0 });
	return [r, i, a] = At([
		s,
		c,
		l
	], !0), [
		r,
		i,
		a,
		o
	];
}, Ln = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (On.test(e)) return [, r, i, a, o] = xn(e, { format: N }), [
		r,
		i,
		a,
		o
	];
	let s, c, l;
	if (n === "mixValue") {
		let n, r = {
			...t,
			d50: !0
		};
		if (n = e.startsWith("color(") ? G(e, r) : K(e, r), n === null) return null;
		[, s, c, l, o] = n;
	} else e.startsWith("color(") ? [, s, c, l, o] = G(e, { d50: !0 }) : [, s, c, l, o] = K(e, { d50: !0 });
	return [r, i, a] = jt([
		s,
		c,
		l
	], !0), [
		r,
		i,
		n === "mixValue" && i === 0 ? O : a,
		o
	];
}, Rn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (kn.test(e)) return [, r, i, a, o] = Sn(e, { format: N }), [
		r,
		i,
		a,
		o
	];
	let s, c, l;
	if (n === "mixValue") {
		let n;
		if (n = e.startsWith("color(") ? G(e, t) : K(e, t), n === null) return null;
		[, s, c, l, o] = n;
	} else e.startsWith("color(") ? [, s, c, l, o] = G(e) : [, s, c, l, o] = K(e);
	return [r, i, a] = Dt([
		s,
		c,
		l
	], !0), [
		r,
		i,
		a,
		o
	];
}, zn = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { format: n = "" } = t, r, i, a, o;
	if (An.test(e)) return [, r, i, a, o] = Cn(e, { format: N }), [
		r,
		i,
		a,
		o
	];
	let s, c, l;
	if (n === "mixValue") {
		let n;
		if (n = e.startsWith("color(") ? G(e, t) : K(e, t), n === null) return null;
		[, s, c, l, o] = n;
	} else e.startsWith("color(") ? [, s, c, l, o] = G(e) : [, s, c, l, o] = K(e);
	return [r, i, a] = Ot([
		s,
		c,
		l
	], !0), [
		r,
		i,
		n === "mixValue" && i === 0 ? O : a,
		o
	];
}, Bn = "color-mix", Vn = (e, t, n, r, i, a, o = !1) => {
	let s = Math.max(0, Math.min(1, n)), c = Math.max(0, Math.min(1, r)), l = s * i, u = c * a, d = l + u, f;
	return f = d === 0 ? o ? [
		0,
		0,
		0
	] : [
		e[0] * i + t[0] * a,
		e[1] * i + t[1] * a,
		e[2] * i + t[2] * a
	] : [
		(e[0] * l + t[0] * u) / d,
		(e[1] * l + t[1] * u) / d,
		(e[2] * l + t[2] * u) / d
	], {
		comps: f,
		alpha: Math.max(0, Math.min(1, d))
	};
}, Hn = (e, t, n, r, i, a, o) => {
	let [s, c, l, u] = n, [d, f, p] = t;
	if (a === "computedValue") return [
		e,
		s ? O : L(d, 16),
		c ? O : L(f, 16),
		l ? O : L(p, 16),
		u ? O : r * i
	];
	let [m, h, g] = o ?? t;
	return [
		"rgb",
		Math.round(m),
		Math.round(h),
		Math.round(g),
		parseFloat((r * i).toFixed(3))
	];
}, q = (e, t, n) => Array.isArray(e) ? e : /^currentcolor$/i.test(e) ? [
	O,
	O,
	O,
	O
] : t(e, n), Un = (e, t, n, r, i, a, o) => {
	let s = e === "srgb" ? Mn : jn, c = q(t, s, {
		colorSpace: e,
		format: P
	}), l = q(n, s, {
		colorSpace: e,
		format: P
	});
	if (!c || !l) return null;
	let [u, d, f, p] = c, [m, h, g, _] = l, v = [
		u === "none" && m === "none",
		d === "none" && h === "none",
		f === "none" && g === "none",
		p === "none" && _ === "none"
	], [[y, b, x, S], [C, w, T, E]] = bt(c, l, !0), { comps: D, alpha: O } = Vn([
		y,
		b,
		x
	], [
		C,
		w,
		T
	], S, E, r, i, !0);
	return Hn(e, D, v, O, o, a, [
		D[0] * 255,
		D[1] * 255,
		D[2] * 255
	]);
}, Wn = (e, t, n, r, i, a, o) => {
	let s = e === "xyz-d50", c = {
		colorSpace: e,
		d50: s,
		format: P
	}, l = q(t, Nn, c), u = q(n, Nn, c);
	if (!l || !u) return null;
	let [d, f, p, m] = l, [h, g, _, v] = u, y = [
		d === "none" && h === "none",
		f === "none" && g === "none",
		p === "none" && _ === "none",
		m === "none" && v === "none"
	], [[b, x, S, C], [w, T, E, D]] = bt(l, u, !0), { comps: O, alpha: k } = Vn([
		b,
		x,
		S
	], [
		w,
		T,
		E
	], C, D, r, i, !0);
	return Hn(e, O, y, k, o, a, s ? kt(O, !0) : wt(O, !0));
}, Gn = (e, t, n, r, i, a, o, s) => {
	let c = e === "hsl" ? Pn : Fn, l = {
		colorSpace: e,
		format: P
	}, u = q(n, c, l), d = q(r, c, l);
	if (!u || !d) return null;
	let [f, p, m, h] = u, [g, _, v, y] = d, b = [
		f === "none" && g === "none",
		p === "none" && _ === "none",
		m === "none" && v === "none",
		h === "none" && y === "none"
	], [[x, S, C, w], [T, E, D, O]] = bt(u, d, !0);
	[x, T] = tt(x, T, t);
	let k = w * i, A = O * a, j = k + A, ee = (x * i + T * a) % 360, te, M;
	j === 0 ? (te = S * i + E * a, M = C * i + D * a) : (te = (S * k + E * A) / j, M = (C * k + D * A) / j);
	let [ne, re, ie] = Mn(`${e}(${ee} ${te} ${M})`), ae = [
		ne / 255,
		re / 255,
		ie / 255
	];
	return Hn(o === "computedValue" ? "srgb" : e, ae, b, j, s, o, [
		ne,
		re,
		ie
	]);
}, Kn = (e, t, n, r, i, a, o, s) => {
	let c = e === "lch" ? Ln : zn, l = {
		colorSpace: e,
		format: P
	}, u = q(n, c, l), d = q(r, c, l);
	if (!u || !d) return null;
	let [f, p, m, h] = u, [g, _, v, y] = d, b = [
		f === "none" && g === "none",
		p === "none" && _ === "none",
		m === "none" && v === "none",
		h === "none" && y === "none"
	], [[x, S, C, w], [T, E, D, O]] = bt(u, d, !0);
	[C, D] = tt(C, D, t);
	let k = w * i, A = O * a, j = k + A, ee = (C * i + D * a) % 360, te, M;
	j === 0 ? (te = x * i + T * a, M = S * i + E * a) : (te = (x * k + T * A) / j, M = (S * k + E * A) / j);
	let ne;
	if (o !== "computedValue") {
		let [, t, n, r] = Zr(`${e}(${te} ${M} ${ee})`);
		ne = [
			t,
			n,
			r
		];
	}
	return Hn(e, [
		te,
		M,
		ee
	], b, j, s, o, ne);
}, qn = (e, t, n, r, i, a, o) => {
	let s = e === "lab" ? In : Rn, c = {
		colorSpace: e,
		format: P
	}, l = q(t, s, c), u = q(n, s, c);
	if (!l || !u) return null;
	let [d, f, p, m] = l, [h, g, _, v] = u, y = [
		d === "none" && h === "none",
		f === "none" && g === "none",
		p === "none" && _ === "none",
		m === "none" && v === "none"
	], [[b, x, S, C], [w, T, E, D]] = bt(l, u, !0), { comps: O, alpha: k } = Vn([
		b,
		x,
		S
	], [
		w,
		T,
		E
	], C, D, r, i), A;
	if (a !== "computedValue") {
		let [, t, n, r] = Zr(`${e}(${O[0]} ${O[1]} ${O[2]})`);
		A = [
			t,
			n,
			r
		];
	}
	return Hn(e, O, y, k, o, a, A);
}, Jn = (e, t, n, r, i, a, o, s) => {
	let c = null;
	return /^srgb(?:-linear)?$/.test(e) ? c = Un(e, n, r, i, a, s, o) : /^xyz(?:-d65|-d50)?$/.test(e) ? c = Wn(e, n, r, i, a, s, o) : /^h(?:sl|wb)$/.test(e) ? c = Gn(e, t, n, r, i, a, s, o) : /^(?:ok)?lch$/.test(e) ? c = Kn(e, t, n, r, i, a, s, o) : /^(?:ok)?lab$/.test(e) && (c = qn(e, n, r, i, a, s, o)), c;
}, Yn = (e) => {
	if (!v(e)) return !1;
	let t = e.toLowerCase().trim();
	return t.endsWith("%") || t.startsWith("calc(") || t.startsWith("var(");
}, Xn = (e) => {
	let t = I(e).filter(Boolean), n = "", r = "";
	return t.length === 1 ? n = t[0] : Yn(t.at(-1)) ? (r = t.pop(), n = t.join(" ")) : (Yn(t[0]) && (r = t.shift()), n = t.join(" ")), {
		color: n,
		pct: r
	};
}, Zn = (e) => {
	let t = [], n = 0, r = !1;
	for (let i of e) {
		let e = Xn(i), { color: a, pct: o } = e;
		if (!a) return null;
		a.toLowerCase().startsWith("var(") && (r = !0, n = NaN);
		let s = o.toLowerCase();
		if (s.startsWith("calc(") || s.startsWith("var(")) r = !0, n = NaN;
		else if (o.endsWith("%")) {
			let e = parseFloat(o);
			if (e < 0) return null;
			r || (n += e);
		}
		t.push(e);
	}
	return {
		colors: t,
		totalPercentage: n
	};
}, Qn = (e) => {
	let t = I(e).filter(Boolean);
	if (t[0] !== "in" || t.length < 2) return null;
	let n = t.indexOf("hue");
	return t.length > 2 && n > -1 ? {
		colorSpace: t[1],
		hueArc: t.slice(2, n).join(" ")
	} : {
		colorSpace: t.slice(1).join(" "),
		hueArc: ""
	};
}, $n = (e) => {
	if (!v(e)) return null;
	e = e.trim();
	let t = e.toLowerCase();
	if (!t.startsWith("color-mix(") || !t.endsWith(")")) return null;
	let n = e.slice(10, -1).trim();
	if (!n) return null;
	let [r, ...i] = I(n, { delimiter: "," }).map((e) => e.trim()), a = [], o = 0, s = Qn(r);
	if (s) {
		let { colorSpace: e, hueArc: t } = s, n = Zn(i);
		return !n || (a.push(...n.colors), o += n.totalPercentage, !a.length) ? null : {
			colors: a,
			colorSpace: e,
			hueArc: t,
			totalPercentage: o
		};
	}
	let c = Zn([r]);
	if (!c) return null;
	a.push(...c.colors), o += c.totalPercentage;
	let l = Zn(i);
	return l ? (a.push(...l.colors), o += l.totalPercentage, {
		colors: a,
		colorSpace: null,
		hueArc: "",
		totalPercentage: o
	}) : null;
}, er = (e, t, n) => {
	let r = e, i = r.toLowerCase();
	for (; v(i) && (i.startsWith("color-mix(") || i.startsWith("var(") || i.startsWith("light-dark("));) {
		let e = n(r, {
			...t,
			format: t.format === "specifiedValue" ? t.format : N
		});
		if (!e) break;
		if (Array.isArray(e)) {
			let [t, n, i, a, o] = e, s = o === 1 || o === void 0 ? "" : ` / ${o}`, c = o === 1 || o === void 0 ? "" : `, ${o}`;
			r = /^rgb$/.test(t) ? o === 1 || o === void 0 ? `rgb(${n}, ${i}, ${a})` : `rgba(${n}, ${i}, ${a}${c})` : /^(?:hsl|hwb|lab|lch|oklab|oklch)$/.test(t) ? `${t}(${n} ${i} ${a}${s})` : `color(${t} ${n} ${i} ${a}${s})`;
			break;
		}
		if (typeof e == "string") {
			if (e === r) break;
			r = e, i = r.toLowerCase();
		} else break;
	}
	return r;
}, tr = (e, t, n) => {
	let r = e.length;
	if (r === 0) return null;
	let i = [], a = 0, o = 0;
	for (let s = 0; s < r; s++) {
		let r = e[s];
		r.color = er(r.color, t, n);
		let c = r.pct.trim();
		if (c === "") i.push(null), o++;
		else {
			let e = parseFloat(c);
			if (Number.isNaN(e) || e < 0 || e > 100) return null;
			i.push(e), a += e;
		}
	}
	let s = Array(r), c = 1;
	if (o === r) {
		let e = 100 / r;
		for (let t = 0; t < r; t++) s[t] = e;
		a = 100;
	} else if (o > 0) {
		let e = 100 - a, t = e > 0 ? e / o : 0;
		for (let e = 0; e < r; e++) i[e] === null ? s[e] = t : s[e] = i[e];
		a = Math.max(100, a);
	} else for (let e = 0; e < r; e++) s[e] = i[e];
	if (a === 0) {
		let e = 100 / r;
		for (let t = 0; t < r; t++) s[t] = e;
		c = 0;
	} else {
		let e = a / 100;
		if (a > 100) {
			for (let t = 0; t < r; t++) s[t] = s[t] / e;
			c = 1;
		} else if (a < 100) {
			for (let t = 0; t < r; t++) s[t] = s[t] / e;
			c = e;
		}
	}
	return {
		colors: e.map((e, t) => ({
			color: e.color,
			ratio: s[t] / 100
		})),
		m: c
	};
}, nr = (e, t, n, r) => {
	let i = e.length, a = [];
	for (let o = 0; o < i; o++) {
		let i = e[o], { color: s, pct: c } = i;
		i.color = er(s, n, r), c === "" && (t > 100 ? i.pct = "0%" : t > 0 && a.push(o));
	}
	if (a.length) {
		let n = L((100 - t) / a.length, 16);
		for (let t of a) {
			let r = e[t];
			r.pct = `${n}%`;
		}
		t += n * a.length;
	}
	return {
		colors: e,
		totalPercentage: t
	};
}, rr = (e, t) => {
	if (!v(e)) return "";
	let n = e.toLowerCase();
	if (n.startsWith("color-mix(") || n.startsWith("light-dark(")) return e;
	if (n.startsWith("color(")) {
		let [n, r, i, a, o] = G(e, t);
		return o === 1 ? `color(${n} ${r} ${i} ${a})` : `color(${n} ${r} ${i} ${a} / ${o})`;
	}
	let r = K(e, t);
	if (Array.isArray(r)) {
		let [e, t, n, i, a] = r;
		return a === 1 ? e === "rgb" ? `${e}(${t}, ${n}, ${i})` : `${e}(${t} ${n} ${i})` : e === "rgb" ? `${e}a(${t}, ${n}, ${i}, ${a})` : `${e}(${t} ${n} ${i} / ${a})`;
	}
	return !v(r) || !r ? "" : r;
}, ir = (e, t, n, r) => {
	let i = [];
	for (let { color: e, pct: t } of n) {
		let n = rr(e, r);
		if (!n) return "";
		i.push(`${n} ${t}`.trim());
	}
	let a = i.join(", ");
	return e ? t ? `color-mix(in ${e} ${t} hue, ${a})` : `color-mix(in ${e}, ${a})` : `color-mix(${a})`;
}, ar = (e, t = {}, n = () => null) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let r = _({
		namespace: Bn,
		name: "resolveColorMix",
		value: e
	}, t), i = h(r);
	if (i !== !1) {
		let { item: e } = i;
		return Array.isArray(e) ? [...e] : v(e) ? e : null;
	}
	let { format: a = "", nullable: o = !1 } = t, s = $n(U(e, t));
	if (!s) return ot(r, a, o);
	let { colorSpace: c, colors: l, hueArc: u, totalPercentage: d } = s;
	!c && a !== "specifiedValue" ? c = "oklab" : c === "xyz" && (c = "xyz-d65");
	let f = nr(l, d, t, n);
	if (l = f.colors, d = f.totalPercentage, a === "specifiedValue") {
		let e = ir(c, u, l, t);
		return e ? (m(r, e), e) : ot(r, a, o);
	}
	let p = tr(l, t, n);
	if (!p) return ot(r, a, o);
	let { colors: g, m: y } = p, b = g.length, x = g[0], S = x.color, C = x.ratio, w = null;
	if (b === 1) w = Jn(c, u, S, S, 1, 0, y, a);
	else for (let e = 1; e < b; e++) {
		let t = g[e], n = t.color, i = t.ratio, s = C + i, l = .5, d = .5;
		if (s > 0 && (l = C / s, d = i / s), w = Jn(c, u, S, n, l, d, e === b - 1 ? y : 1, a), !Array.isArray(w)) return ot(r, a, o);
		let [f, p, m, h, _] = w, v = _ === 1 || _ === void 0 ? "" : ` / ${_}`, x = _ === 1 || _ === void 0 ? "" : `, ${_}`;
		S = /^rgb$/.test(f) ? _ === 1 || _ === void 0 ? `rgb(${p}, ${m}, ${h})` : `rgba(${p}, ${m}, ${h}${x})` : /^(?:hsl|hwb|lab|lch|oklab|oklch)$/.test(f) ? `${f}(${p} ${m} ${h}${v})` : `color(${f} ${p} ${m} ${h}${v})`, C = s;
	}
	return w ? (m(r, w), w) : ot(r, a, o);
}, or = "relative-color", { CloseParen: sr, Comment: cr, Delim: lr, Dimension: ur, EOF: dr, Function: fr, Ident: pr, Number: mr, OpenParen: hr, Percentage: gr, Whitespace: _r } = s, { HasNoneKeywords: vr } = e, yr = /* @__PURE__ */ new Map([
	["color", [
		"r",
		"g",
		"b",
		"alpha"
	]],
	["hsl", [
		"h",
		"s",
		"l",
		"alpha"
	]],
	["hsla", [
		"h",
		"s",
		"l",
		"alpha"
	]],
	["hwb", [
		"h",
		"w",
		"b",
		"alpha"
	]],
	["lab", [
		"l",
		"a",
		"b",
		"alpha"
	]],
	["lch", [
		"l",
		"c",
		"h",
		"alpha"
	]],
	["oklab", [
		"l",
		"a",
		"b",
		"alpha"
	]],
	["oklch", [
		"l",
		"c",
		"h",
		"alpha"
	]],
	["rgb", [
		"r",
		"g",
		"b",
		"alpha"
	]],
	["rgba", [
		"r",
		"g",
		"b",
		"alpha"
	]]
]), br = /* @__PURE__ */ new Set([
	"lab",
	"lch",
	"oklab",
	"oklch"
]), xr = /* @__PURE__ */ new Set([
	"hsl",
	"hsla",
	"hwb"
]), Sr = /* @__PURE__ */ new Set([
	"color(",
	"hsl(",
	"hsla(",
	"hwb(",
	"lab(",
	"lch(",
	"oklab(",
	"oklch(",
	"rgb(",
	"rgba("
]), Cr = /* @__PURE__ */ new Set([
	"abs(",
	"acos(",
	"asin(",
	"atan(",
	"atan2(",
	"clamp(",
	"cos(",
	"exp(",
	"hypot(",
	"log(",
	"max(",
	"min(",
	"mod(",
	"pow(",
	"rem(",
	"round(",
	"sign(",
	"sin(",
	"sqrt(",
	"tan("
]), wr = /* @__PURE__ */ new Set([
	"abs(",
	"cos(",
	"sign(",
	"sin(",
	"tan("
]), Tr = RegExp(`^(?:${le})\\(\\s*from\\s+(${we}|${Ee})\\s+`, "i"), Er = RegExp(`^(${le})\\(\\s*from\\s+`, "i"), Dr = RegExp(`^(?:${le})\\(\\s*from\\s+`, "i"), Or = (e, t) => {
	let n = [];
	for (let r of e) if (r.length === 1) {
		let [e] = r;
		y(e) && n.push(e);
	} else if (r.length) {
		let e = sn(r.join(""), { format: t });
		n.push(e);
	}
	return n;
}, kr = (e) => {
	for (let t = e.length - 1; t >= 0; t--) if (e[t] !== " ") return e[t];
}, Ar = (e, t = {}) => {
	if (!Array.isArray(e)) throw TypeError(`${e} is not an array.`);
	let { colorSpace: n = "", format: r = "" } = t, i = yr.get(n);
	if (!i) return null;
	let a = /* @__PURE__ */ new Set(), o = [
		[],
		[],
		[],
		[]
	], s = 0, c = 0, l = "", u = !1;
	for (let n of e) {
		if (!Array.isArray(n)) throw TypeError(`${n} is not an array.`);
		let [e, r, , , d] = n, f = o[s];
		if (Array.isArray(f)) switch (e) {
			case lr:
				if (l) {
					if ((r === "+" || r === "-") && u && !wr.has(l)) return null;
					u = !1, f.push(r);
				}
				break;
			case ur: {
				if (!l || !wr.has(l)) return null;
				let e = cn(n, t);
				v(e) ? f.push(e) : f.push(r);
				break;
			}
			case fr:
				f.push(r), l = r, c++, Cr.has(r) && a.add(c);
				break;
			case pr:
				if (!i.includes(r)) return null;
				f.push(r), l || s++;
				break;
			case mr:
				f.push(Number(d?.value)), l || s++;
				break;
			case hr:
				f.push(r), c++;
				break;
			case sr:
				l && (f.at(-1) === " " ? f[f.length - 1] = r : f.push(r), a.has(c) && a.delete(c), c--, c === 0 && (l = "", s++));
				break;
			case gr:
				if (!l) return null;
				if (!wr.has(l)) {
					let e = kr(f);
					if (e === "+" || e === "-") return null;
					u = e !== "*" && e !== "/";
				}
				f.push(Number(d?.value) / 100);
				break;
			case _r:
				if (f.length && l) {
					let e = f.at(-1);
					(typeof e == "number" || v(e) && !e.endsWith("(") && e !== " ") && f.push(r);
				}
				break;
			default: e !== cr && e !== dr && l && f.push(r);
		}
	}
	return Or(o, r);
}, jr = (e, t, n, r) => {
	let { colorScheme: i, format: a } = n, [, o] = I(e.replace(RegExp(`^${t}\\(`), "").replace(/\)$/, "")), s = r(o, {
		colorScheme: i,
		format: Oe
	});
	if (!s) return null;
	if (a === "specifiedValue") return e.replace(o, s);
	let c = r(s, n);
	return v(c) ? e.replace(o, c) : null;
}, Mr = (e, t, n) => {
	let [, r] = e.match(Er), i = {
		...t,
		colorSpace: r
	}, [, a] = e.split(Dr), o = c({ css: a }), s = [], l = 0, u = 0;
	for (let [e, t] of o) {
		switch (u++, e) {
			case fr:
			case hr:
				s.push(t), l++;
				break;
			case sr:
				s.at(-1) === " " ? s[s.length - 1] = t : s.push(t), l--;
				break;
			case _r: {
				let e = s.at(-1);
				v(e) && !e.endsWith("(") && e !== " " && s.push(t);
				break;
			}
			default: e !== cr && e !== dr && s.push(t);
		}
		if (l === 0) break;
	}
	let d = Lr(s.join("").trim(), i, n);
	if (d === null) return null;
	let f = Ar(o.slice(u), i);
	if (f === null) return null;
	let [p, m, h, g] = f, _ = "";
	return _ = y(g) ? ` ${p} ${m} ${h} / ${g})` : ` ${f.join(" ")})`, e.replace(a, `${d}${_}`);
}, Nr = (e, t = {}, n = () => null) => {
	if (!v(e) || (e = e.trim(), !e)) return null;
	if (!Dr.test(e)) return e;
	let { currentColor: r, format: i } = t, a = _({
		namespace: or,
		name: "extractOriginColor",
		value: e
	}, t), o = h(a);
	if (o !== !1) return o.item;
	if (/currentcolor/i.test(e)) {
		if (!r) return m(a, null), null;
		e = e.replace(/currentcolor/gi, r);
	}
	let [, s] = e.match(Er), l = {
		...t,
		colorSpace: s
	};
	if (e.includes("light-dark(")) {
		let t = jr(e, s, l, n);
		if (t === null) return m(a, null), null;
		e = t;
	}
	if (Tr.test(e)) {
		let [, t] = e.match(Tr), [, r] = e.split(t);
		if (/^[a-z]+$/.test(t)) {
			if (!/^transparent$/.test(t) && !Object.hasOwn(mn, t)) return m(a, null), null;
		} else if (i === "specifiedValue") {
			let r = n(t, l);
			e = e.replace(t, r);
		}
		if (i === "specifiedValue") {
			let t = Ar(c({ css: r }), l);
			if (t === null) return m(a, null), null;
			let [n, i, o, s] = t, u = "";
			u = y(s) ? ` ${n} ${i} ${o} / ${s})` : ` ${t.join(" ")})`, r !== u && (e = e.replace(r, u));
		}
	} else {
		let t = Mr(e, l, n);
		if (t === null) return m(a, null), null;
		e = t;
	}
	return m(a, e), e;
}, Pr = (e) => {
	for (let t of e) {
		let [e, n] = t;
		if (e === fr && n === "var(") return !0;
	}
	return !1;
}, Fr = (e) => {
	for (let t = 0; t < e.length - 1; t++) {
		let [n, r] = e[t];
		if (n === fr && Sr.has(r)) for (let n = t + 1; n < e.length; n++) {
			let [t, r] = e[n];
			if (t !== _r && t !== cr) {
				if (t === pr && r === "from") return !0;
				break;
			}
		}
	}
	return !1;
}, Ir = (e) => {
	if (!e) return null;
	let { alpha: t, channels: n, colorNotation: r, syntaxFlags: i } = e, a;
	a = Number.isNaN(Number(t)) ? i instanceof Set && i.has(vr) ? O : 0 : L(Number(t), 8);
	let o, s, c;
	[o, s, c] = n;
	let l;
	if (br.has(r)) {
		let e = i instanceof Set && i.has(vr);
		o = Number.isNaN(o) ? e ? O : 0 : L(o, 16), s = Number.isNaN(s) ? e ? O : 0 : L(s, 16), c = Number.isNaN(c) ? e ? O : 0 : L(c, 16), l = a === 1 ? `${r}(${o} ${s} ${c})` : `${r}(${o} ${s} ${c} / ${a})`;
	} else if (xr.has(r)) {
		Number.isNaN(o) && (o = 0), Number.isNaN(s) && (s = 0), Number.isNaN(c) && (c = 0);
		let [e, t, n] = Mn(`${r}(${o} ${s} ${c} / ${a})`);
		e = L(e / 255, 10), t = L(t / 255, 10), n = L(n / 255, 10), l = a === 1 ? `color(srgb ${e} ${t} ${n})` : `color(srgb ${e} ${t} ${n} / ${a})`;
	} else {
		let e = r === "rgb" ? "srgb" : r, t = i instanceof Set && i.has(vr);
		o = Number.isNaN(o) ? t ? O : 0 : L(o, 10), s = Number.isNaN(s) ? t ? O : 0 : L(s, 10), c = Number.isNaN(c) ? t ? O : 0 : L(c, 10), l = a === 1 ? `color(${e} ${o} ${s} ${c})` : `color(${e} ${o} ${s} ${c} / ${a})`;
	}
	return l;
};
function Lr(e, n = {}, r = () => null) {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let i = { ...n }, { format: o = "" } = i, s = c({ css: e });
	if (Pr(s)) {
		if (o !== "specifiedValue") throw SyntaxError("Unexpected token var( found.");
		return e;
	}
	if (!Fr(s)) return e;
	let l = _({
		namespace: or,
		name: "resolveRelativeColor",
		value: e
	}, i), u = h(l);
	if (u !== !1) return u.item;
	let d = Nr(e, i, r);
	if (d === null) return m(l, null), null;
	let f = d;
	if (o === "specifiedValue") return f.startsWith("rgba(") ? f.replace("rgba(", "rgb(") : f.startsWith("hsla(") ? f.replace("hsla(", "hsl(") : f;
	let p = c({ css: f }), g = a(p), y = Ir(t(g));
	return y === null ? (m(l, null), null) : (m(l, y), y);
}
// src/resolvers/resolve-color.ts
var Rr = "resolve-color", zr = "rgba(0, 0, 0, 0)", Br = RegExp(`^(?:${we})$`, "i"), Vr = RegExp(`^color\\(\\s*(${_e})\\s*\\)$`, "i"), Hr = /^currentColor$/i, Ur = new RegExp(A, "i"), Wr = /^(?:(?:ok)?l(?:ab|ch)|color(?:-mix)?|hsla?|hwb|rgba?|var)\(/i, Gr = new RegExp(ve, "i"), Kr = new RegExp(Ee, "i"), qr = new RegExp(ye, "i"), Jr = new RegExp(ee, "i"), Yr = /^[a-z]+$/i, Xr = /^(?:specifi|comput)edValue$/, Zr = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { colorSpace: n = "", format: r = "", nullable: i = !1 } = t, a = _({
		namespace: Rr,
		name: "resolveColorValue",
		value: e
	}, t), o = h(a);
	if (o !== !1) {
		let { item: e } = o;
		return Array.isArray(e) ? [...e] : e;
	}
	if (!Br.test(e)) {
		let e = z(r, i);
		return e === null ? (m(a, null), null) : (m(a, e), v(e), e);
	}
	let s = "", c = 0, l = 0, u = 0, d = 0;
	if (Hr.test(e)) {
		if (r === "specifiedValue") return m(a, e), e;
	} else if (Yr.test(e)) {
		if (Object.hasOwn(mn, e)) {
			if (r === "specifiedValue") return m(a, e), e;
			[c, l, u] = mn[e], d = 1;
		} else switch (r) {
			case Oe: return e === "transparent" ? (m(a, e), e) : (m(a, ""), "");
			case P:
				if (e === "transparent") {
					let e = [
						"rgb",
						0,
						0,
						0,
						0
					];
					return m(a, e), [...e];
				}
				return m(a, null), null;
			case N:
			default: {
				if (i && e !== "transparent") return m(a, null), null;
				let t = [
					"rgb",
					0,
					0,
					0,
					0
				];
				return m(a, t), [...t];
			}
		}
	} else if (e[0] === "#") [c, l, u, d] = Nt(e);
	else if (e.startsWith("hsl")) [, c, l, u, d] = vn(e, t);
	else if (e.startsWith("hwb")) [, c, l, u, d] = yn(e, t);
	else if (/^l(?:ab|ch)/.test(e)) {
		let n, i, o;
		if (e.startsWith("lab") ? [s, n, i, o, d] = bn(e, t) : [s, n, i, o, d] = xn(e, t), Xr.test(r)) {
			let e = [
				s,
				n,
				i,
				o,
				d
			];
			return m(a, e), [...e];
		}
		[c, l, u] = kt([
			n,
			i,
			o
		]);
	} else if (/^okl(?:ab|ch)/.test(e)) {
		let n, i, o;
		if (e.startsWith("oklab") ? [s, n, i, o, d] = Sn(e, t) : [s, n, i, o, d] = Cn(e, t), Xr.test(r)) {
			let e = [
				s,
				n,
				i,
				o,
				d
			];
			return m(a, e), [...e];
		}
		[c, l, u] = wt([
			n,
			i,
			o
		]);
	} else [, c, l, u, d] = _n(e, t);
	if (r === "mixValue" && n === "srgb") {
		let e = [
			"srgb",
			c / 255,
			l / 255,
			u / 255,
			d
		];
		return m(a, e), [...e];
	}
	let f = [
		"rgb",
		Math.round(c),
		Math.round(l),
		Math.round(u),
		d
	];
	return m(a, f), [...f];
}, J = (e, t = {}) => {
	if (!v(e)) return null;
	let n = e.trim(), { colorSpace: r = "", format: i = "", nullable: a = !1 } = t, o = _({
		namespace: Rr,
		name: "resolveColorFunc",
		value: n
	}, t), s = h(o);
	if (s !== !1) {
		let { item: e } = s;
		return Array.isArray(e) ? [...e] : e;
	}
	if (!Vr.test(n)) {
		let e = z(i, a);
		return e === null ? (m(o, null), null) : (m(o, e), v(e), e);
	}
	let [c, l, u, d, f] = G(n, t);
	if (Xr.test(i) || i === "mixValue" && c === r) {
		let e = [
			c,
			l,
			u,
			d,
			f
		];
		return m(o, e), [...e];
	}
	let p = parseFloat(`${l}`), g = parseFloat(`${u}`), y = parseFloat(`${d}`), b = R(`${f}`), [x, S, C] = wt([
		p,
		g,
		y
	], !0), w = [
		"rgb",
		x,
		S,
		C,
		b
	];
	return m(o, w), [...w];
}, Y = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	e = e.trim();
	let { colorScheme: n = "normal", currentColor: r = "", format: i = N, nullable: a = !1 } = t, o = _({
		namespace: Rr,
		name: "resolve",
		value: e
	}, t), s = h(o);
	if (s !== !1) return s.item;
	if (Jr.test(e)) {
		if (i === "specifiedValue") return m(o, e), e;
		let n = fn(e, t);
		if (n === null) {
			let e = i === "hex" || i === "hexAlpha" || a ? null : zr;
			return m(o, e), e;
		}
		e = n;
	}
	if (t.format !== i && (t.format = i), Gr.test(e) && e.endsWith(")")) {
		let [r = "", a = ""] = I(e.replace(Gr, "").replace(/\)$/, ""), { delimiter: "," });
		if (r && a) {
			if (i === "specifiedValue") {
				let e = Y(r, t), n = Y(a, t), i = e && n ? `light-dark(${e}, ${n})` : "";
				return m(o, i), i;
			}
			let e = Y(n === "dark" ? a : r, t);
			return m(o, e), e;
		}
		let s;
		return s = i === "specifiedValue" ? "" : i === "hex" || i === "hexAlpha" ? null : zr, m(o, s), s;
	}
	if (qr.test(e)) {
		let n = Lr(e, t, Y);
		if (i === "computedValue") {
			let e = n === null && !a ? zr : n;
			return m(o, e), e;
		}
		if (i === "specifiedValue") {
			let e = n === null ? "" : n;
			return m(o, e), e;
		}
		e = n === null ? "" : n;
	}
	Ur.test(e) && (e = U(e, t));
	let c = "", l = NaN, u = NaN, d = NaN, f = NaN;
	if (e === "transparent") {
		let t;
		switch (i) {
			case Oe:
				t = e;
				break;
			case "hex":
				t = null;
				break;
			case "hexAlpha":
				t = "#00000000";
				break;
			default: t = zr;
		}
		return m(o, t), t;
	}
	if (/^currentcolor$/i.test(e)) {
		if (i === "specifiedValue") return m(o, e), e;
		if (r) {
			let e;
			if (e = r.startsWith("color-mix(") ? ar(r, t, Y) : r.startsWith("color(") ? J(r, t) : Zr(r, t), e === null) return m(o, null), null;
			[c, l, u, d, f] = e;
		} else {
			let t = i === "computedValue" ? zr : e;
			if (i === "computedValue") return m(o, t), t;
		}
	} else if (i === "specifiedValue") {
		let n = "";
		if (e.startsWith("color-mix(")) {
			let r = ar(e, t, Y);
			v(r) && r && (n = r);
		} else if (e.startsWith("color(")) {
			let r = J(e, t);
			if (Array.isArray(r)) {
				let [e, t, i, a, o] = r;
				n = o === 1 ? `color(${e} ${t} ${i} ${a})` : `color(${e} ${t} ${i} ${a} / ${o})`;
			}
		} else {
			let r = Zr(e, t);
			if (Array.isArray(r)) {
				let [e, t, i, a, o] = r;
				n = e === "rgb" ? o === 1 ? `${e}(${t}, ${i}, ${a})` : `${e}a(${t}, ${i}, ${a}, ${o})` : o === 1 ? `${e}(${t} ${i} ${a})` : `${e}(${t} ${i} ${a} / ${o})`;
			} else r && (n = r);
		}
		return m(o, n), n;
	} else if (e.startsWith("color-mix(")) {
		r && (e = e.replace(/currentcolor/gi, r)), e = e.replace(/transparent/g, zr);
		let n = ar(e, t, Y);
		if (n === null) return m(o, null), null;
		[c, l, u, d, f] = n;
	} else if (e.startsWith("color(")) {
		let n = J(e, t);
		if (n === null) return m(o, null), null;
		[c, l, u, d, f] = n;
	} else if (e) {
		let n = Zr(e, t);
		if (n === null) return m(o, null), null;
		[c, l, u, d, f] = n;
	}
	let p = "";
	switch (i) {
		case "hex":
		case "hexAlpha":
			p = Number.isNaN(l) || Number.isNaN(u) || Number.isNaN(d) || Number.isNaN(f) || i === "hex" && f === 0 ? null : Mt([
				l,
				u,
				d,
				i === "hex" ? 1 : f
			]);
			break;
		default: p = c === "rgb" ? f === 1 ? `${c}(${l}, ${u}, ${d})` : `${c}a(${l}, ${u}, ${d}, ${f})` : [
			"lab",
			"lch",
			"oklab",
			"oklch"
		].includes(c) ? f === 1 ? `${c}(${l} ${u} ${d})` : `${c}(${l} ${u} ${d} / ${f})` : f === 1 ? `color(${c} ${l} ${u} ${d})` : `color(${c} ${l} ${u} ${d} / ${f})`;
	}
	return m(o, p), p;
}, Qr = (e, t = {}) => {
	if (!v(e)) return !1;
	let n = e.trim();
	if (!n) return !1;
	if (Yr.test(n)) {
		let e = n.toLowerCase();
		return e === "currentcolor" || e === "transparent" || Object.hasOwn(mn, e);
	}
	return Br.test(n) || Kr.test(n) ? !0 : Wr.test(n) ? !!Y(n, {
		...t,
		nullable: !0
	}) : !1;
}, { CloseParen: $r, Comma: ei, Comment: ti, Delim: ni, Dimension: ri, EOF: ii, Function: ai, Ident: oi, Number: si, OpenParen: ci, Percentage: li, Whitespace: ui } = s, di = RegExp(`^(?:${T})$`, "i"), fi = /^(?:calc|min|max|clamp)\(/i, pi = /^(?:e|pi|-?infinity|NaN)$/i, mi = (e) => {
	let t = e.tokens[e.index];
	for (; t && (t[0] === ui || t[0] === ti);) e.index++, t = e.tokens[e.index];
}, hi = (e, t, n) => {
	for (let r = t; r >= 0 && r < e.length; r += n) {
		let [t] = e[r];
		if (t === ui) return !0;
		if (t !== ti) break;
	}
	return !1;
}, gi = (e) => {
	let t = vi(e);
	if (t === null) return null;
	mi(e);
	let n = e.tokens[e.index];
	for (; n && n[0] === ni && (n[1] === "*" || n[1] === "/");) {
		let r = n[1];
		e.index++;
		let i = vi(e);
		if (i === null || (r === "*" ? t !== 0 && i !== 0 : i !== 0)) return null;
		t += i, mi(e), n = e.tokens[e.index];
	}
	return t;
}, _i = (e) => {
	let t = gi(e);
	if (t === null) return null;
	let n = e.tokens[e.index];
	for (; n && n[0] === ni && (n[1] === "+" || n[1] === "-");) {
		if (!hi(e.tokens, e.index - 1, -1) || !hi(e.tokens, e.index + 1, 1) || (e.index++, gi(e) !== t)) return null;
		n = e.tokens[e.index];
	}
	return t;
};
function vi(e) {
	mi(e);
	let t = e.tokens[e.index];
	if (!t || t[0] === ii) return null;
	e.index++;
	let [n, , , , r] = t;
	switch (n) {
		case si: return 0;
		case li: return 1;
		case ri: return di.test(r.unit) ? 1 : null;
		case oi: return pi.test(r.value) ? 0 : null;
		case ci:
		case ai: {
			let t = n === ai ? r.value.toLowerCase() : "calc";
			if (![
				"calc",
				"min",
				"max",
				"clamp"
			].includes(t)) return null;
			let i = _i(e);
			if (i === null) return null;
			let a = 1, o = e.tokens[e.index];
			for (; o && o[0] === ei;) {
				if (e.index++, _i(e) !== i) return null;
				a++, o = e.tokens[e.index];
			}
			let s = e.tokens[e.index];
			return !s || s[0] !== $r || t === "calc" && a !== 1 || t === "clamp" && a !== 3 ? null : (e.index++, i);
		}
		default: return null;
	}
}
var yi = (e) => {
	if (!v(e) || !fi.test(e)) return !1;
	let t = [], n = {
		tokens: c({ css: e }, { onParseError: t.push.bind(t) }),
		index: 0
	}, r = vi(n);
	mi(n);
	let i = n.tokens[n.index];
	return t.length === 0 && r === 1 && !!i && i[0] === ii;
}, { Function: bi } = s, xi = `${E}(?:${w})`, X = `${`${E}(?:${T})|0`}|${k}`, Si = `${D}(?:${T}|%)|0`, Ci = `${D}(?:${T})|0`, Z = "center", wi = "left|right", Ti = "top|bottom", Ei = "start|end", Di = `${wi}|x-(?:${Ei})`, Oi = `${Ti}|y-(?:${Ei})`, ki = `block-(?:${Ei})`, Ai = `inline-(?:${Ei})`, ji = `${Z}|${Di}|${Oi}|${ki}|${Ai}|${X}`, Mi = [
	`(?:${Z}|${Di})\\s+(?:${Z}|${Oi})`,
	`(?:${Z}|${Oi})\\s+(?:${Z}|${Di})`,
	`(?:${Z}|${Di}|${X})\\s+(?:${Z}|${Oi}|${X})`,
	`(?:${Z}|${ki})\\s+(?:${Z}|${Ai})`,
	`(?:${Z}|${Ai})\\s+(?:${Z}|${ki})`,
	`(?:${Z}|${Ei})\\s+(?:${Z}|${Ei})`
].join("|"), Ni = [
	`(?:${Di})\\s+(?:${X})\\s+(?:${Oi})\\s+(?:${X})`,
	`(?:${Oi})\\s+(?:${X})\\s+(?:${Di})\\s+(?:${X})`,
	`(?:${ki})\\s+(?:${X})\\s+(?:${Oi})\\s+(?:${X})`,
	`(?:${Ai})\\s+(?:${X})\\s+(?:${ki})\\s+(?:${X})`,
	`(?:${Ei})\\s+(?:${X})\\s+(?:${Ei})\\s+(?:${X})`
].join("|"), Pi = "(?:clos|farth)est-(?:corner|side)", Fi = [
	`${Pi}(?:\\s+${Pi})?`,
	`${Ci}`,
	`(?:${Si})\\s+(?:${Si})`
].join("|"), Ii = "circle|ellipse", Li = `from\\s+${xi}`, Q = `at\\s+(?:${ji}|${Mi}|${Ni})`, Ri = `to\\s+(?:(?:${wi})(?:\\s(?:${Ti}))?|(?:${Ti})(?:\\s(?:${wi}))?)`, $ = `in\\s+(?:${me}|${ue})`, zi = [
	/ellipse/,
	/farthest-corner/,
	/at\s+center/
], Bi = [/at\s+center/], Vi = {
	format: Oe,
	nullable: !0
}, Hi = /^(?:repeating-)?conic-gradient$/, Ui = /^(?:repeating-)?linear-gradient$/, Wi = /^(?:repeating-)?radial-gradient$/;
RegExp(`^${De}$`, "i");
var Gi = RegExp(`^(?:${T})$`, "i"), Ki = /^to\s+bottom$/i, qi = [
	RegExp(`^${Li}(?:\\s+${Q})?(?:\\s+${$})?$`, "i"),
	RegExp(`^${Q}(?:\\s+${$})?$`, "i"),
	RegExp(`^${$}(?:\\s+${Li})?(?:\\s+${Q})?$`, "i")
], Ji = [RegExp(`^(?:${xi}|${Ri})(?:\\s+${$})?$`, "i"), RegExp(`^${$}(?:\\s+(?:${xi}|${Ri}))?$`, "i")], Yi = [
	RegExp(`^(?:${Ii})(?:\\s+(?:${Fi}))?(?:\\s+${Q})?(?:\\s+${$})?$`, "i"),
	RegExp(`^(?:${Fi})(?:\\s+(?:${Ii}))?(?:\\s+${Q})?(?:\\s+${$})?$`, "i"),
	RegExp(`^${Q}(?:\\s+${$})?$`, "i"),
	RegExp(`^${$}(?:\\s+${Ii})(?:\\s+(?:${Fi}))?(?:\\s+${Q})?$`, "i"),
	RegExp(`^${$}(?:\\s+${Fi})(?:\\s+(?:${Ii}))?(?:\\s+${Q})?$`, "i"),
	RegExp(`^${$}(?:\\s+${Q})?$`, "i")
], Xi = (e) => {
	if (!v(e)) return {
		line: "",
		valid: !1
	};
	let t = e.trim();
	if (!t) return {
		line: "",
		valid: !0
	};
	let n = st(t);
	if (n.length === 0) return {
		line: e,
		valid: !1
	};
	let r = n.join(" ");
	if (!Ji.some((e) => e.test(r))) return {
		line: e,
		valid: !1
	};
	let i = r.replace(/\s+/g, " ").trim();
	return Ki.test(i) ? {
		line: "",
		valid: !0
	} : {
		line: i.replace(/^to\sbottom\s/i, "").replace(/\sto\sbottom$/i, "").trim(),
		valid: !0
	};
}, Zi = (e) => {
	if (!v(e)) return {
		line: "",
		valid: !1
	};
	let t = e.trim();
	if (!t) return {
		line: "",
		valid: !0
	};
	let n = st(t);
	if (n.length === 0) return {
		line: e,
		valid: !1
	};
	let r = n.join(" ");
	if (!Yi.some((e) => e.test(r))) return {
		line: e,
		valid: !1
	};
	let i = r;
	for (let e of zi) i = i.replace(e, "");
	return i = i.replace(/\s{2,}/g, " ").trim(), {
		line: i,
		valid: !0
	};
}, Qi = (e) => {
	if (!v(e)) return {
		line: "",
		valid: !1
	};
	let t = e.trim();
	if (!t) return {
		line: "",
		valid: !0
	};
	let n = st(t);
	if (n.length === 0) return {
		line: e,
		valid: !1
	};
	let r = n.join(" ");
	if (!qi.some((e) => e.test(r))) return {
		line: e,
		valid: !1
	};
	let i = r;
	for (let e of Bi) i = i.replace(e, "");
	return i = i.replace(/\s{2,}/g, " ").trim(), {
		line: i,
		valid: !0
	};
}, $i = (e, t) => {
	if (v(e) && v(t)) {
		let n = e.trim(), r = t.trim();
		if (Ui.test(r)) return Xi(n);
		if (Wi.test(r)) return Zi(n);
		if (Hi.test(r)) return Qi(n);
	}
	return {
		line: e,
		valid: !1
	};
}, ea = (e) => st(e), ta = (e, t) => {
	if (!v(e)) return !1;
	let n = e.trim();
	if (!n) return !1;
	let r = c({ css: n }).filter((e) => e[0] !== s.Whitespace && e[0] !== s.Comment && e[0] !== s.EOF);
	if (r.length === 1) {
		let [e, n, , , i] = r[0];
		if (e === s.Number) return !t && Number(n) === 0;
		if (e === s.Percentage) return !0;
		if (e === s.Dimension) {
			let e = i.unit.toLowerCase();
			return t ? w.split("|").some((t) => RegExp(`^${t}$`, "i").test(e)) : Gi.test(e);
		}
		return !1;
	}
	return !t && yi(n);
}, na = (e, t, n = {}) => {
	if (!Array.isArray(e) || e.length <= 1) return {
		colorStops: e,
		valid: !1
	};
	let r = Hi.test(t), i = [], a = "";
	for (let [t, o] of e.entries()) {
		if (!v(o)) return {
			colorStops: e,
			valid: !1
		};
		let s = ea(o), [c, ...l] = s;
		if (!c || s.length > 3) return {
			colorStops: e,
			valid: !1
		};
		if (s.length === 1 && ta(c, r)) {
			if (t === 0 || a === "hint") return {
				colorStops: e,
				valid: !1
			};
			a = "hint";
			let r = yi(c) ? U(c, n) : c;
			i.push(r);
			continue;
		}
		let u = [];
		for (let t of l) {
			if (!ta(t, r)) return {
				colorStops: e,
				valid: !1
			};
			let i = yi(t) ? U(t, n) : t;
			u.push(i);
		}
		let d = I(c).join(" ");
		if (!Qr(d, Vi)) return {
			colorStops: e,
			valid: !1
		};
		let f = Y(d, n);
		if (!f || !Qr(f, Vi)) return {
			colorStops: e,
			valid: !1
		};
		a = "color";
		let p = u.length > 0 ? `${f} ${u.join(" ")}` : f;
		i.push(p);
	}
	return a === "color" ? {
		colorStops: i,
		valid: !0
	} : {
		colorStops: e,
		valid: !1
	};
}, ra = "gradient", ia = {
	format: Oe,
	nullable: !0
}, aa = RegExp(`^${De}$`, "i"), oa = (e, t = {}) => {
	if (!v(e)) return null;
	let n = e.trim(), i = _({
		namespace: ra,
		name: "parseGradient",
		value: n
	}, t), a = h(i);
	if (a !== !1) return a.item;
	let s = c({ css: n }), l = o(s);
	if (l.length !== 1 || !r(l[0])) return m(i, null), null;
	let u = l[0], d = u.getName().toLowerCase();
	if (!aa.test(d)) return m(i, null), null;
	let f = u.value.map((e) => e.toString()).join("");
	if (f) {
		let [e, ...r] = I(f, {
			delimiter: ",",
			preserveComment: !0
		});
		if (!e) return m(i, null), null;
		let [a] = ea(e);
		if (a && Qr(I(a).join(" "), ia)) {
			r.unshift(e);
			let { colorStops: a, valid: o } = na(r, d, t);
			if (o) {
				let e = {
					value: n,
					type: d,
					colorStopList: a
				};
				return m(i, e), e;
			}
		} else if (r.length > 1) {
			let { line: a, valid: o } = $i(I(e).join(" "), d), { colorStops: s, valid: c } = na(r, d, t);
			if (o && c) {
				let e = {
					value: n,
					type: d,
					gradientLine: a,
					colorStopList: s
				};
				return m(i, e), e;
			}
		}
	}
	return m(i, null), null;
}, sa = (e, t = {}) => {
	let n = { ...t }, r = $e(n);
	if (e && e.length <= r) {
		let t = oa(e, n);
		if (t) {
			let { type: e, gradientLine: n, colorStopList: r } = t;
			return n ? `${e}(${n}, ${r.join(", ")})` : `${e}(${r.join(", ")})`;
		}
	}
	return n.format === "specifiedValue" ? "" : O;
}, ca = (e, t = {}) => !!oa(e, { ...t }), la = (e, t = {}) => Y(e, { ...t }), ua = (e, t = {}) => !!Qr(e, {
	...t,
	nullable: !0
}), da = "convert", fa = new RegExp(A), pa = new RegExp(ye), ma = new RegExp(ee), ha = (e, t) => {
	if (!v(e)) return null;
	e = e.trim();
	let n = $e(t);
	if (!e || e.length > n) return null;
	let r = _({
		namespace: da,
		name: "preProcess",
		value: e
	}, t), i = h(r);
	if (i !== !1) return i.item;
	let a = e;
	if (ma.test(e)) {
		let n = fn(e, t);
		if (!v(n)) return m(r, null), null;
		a = n;
	}
	if (pa.test(a)) {
		let e = Lr(a, t);
		if (!v(e)) return m(r, null), null;
		a = e;
	} else fa.test(a) && (a = U(a, t));
	if (a.startsWith("color-mix")) {
		let e = Y(a, {
			...t,
			format: N,
			nullable: !0
		});
		a = typeof e == "string" ? e : null;
	}
	return !a || a.length > n ? (m(r, null), null) : (m(r, a), a);
}, ga = (e, t, n) => (r, i = {}) => {
	if (!v(r)) throw TypeError(`${r} is not a string.`);
	let a = { ...i }, o = ha(r, a);
	if (o === null) return [
		0,
		0,
		0,
		0
	];
	let s = o.toLowerCase(), c = _({
		namespace: da,
		name: e,
		value: s
	}, a), l = h(c);
	if (l !== !1) return [...l.item];
	let u = n(s, {
		...a,
		format: t
	});
	return m(c, u), [...u];
}, _a = (e) => rt(e), va = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let n = { ...t }, r = ha(e, n);
	if (r === null) return null;
	let i = r.toLowerCase(), a = _({
		namespace: da,
		name: "colorToHex",
		value: i
	}, n), o = h(a);
	if (o !== !1) return o.item;
	let s = Y(i, {
		...n,
		nullable: !0,
		format: n.alpha ? "hexAlpha" : "hex"
	});
	return v(s) ? (m(a, s), s) : (m(a, null), null);
}, ya = ga("colorToHsl", "hsl", Pn), ba = ga("colorToHwb", "hwb", Fn), xa = ga("colorToLab", "lab", In), Sa = ga("colorToLch", "lch", Ln), Ca = ga("colorToOklab", "oklab", Rn), wa = ga("colorToOklch", "oklch", zn), Ta = ga("colorToRgb", "rgb", Mn), Ea = (e, t = {}) => {
	if (!v(e)) throw TypeError(`${e} is not a string.`);
	let n = { ...t }, r = ha(e, n);
	if (r === null) return [
		0,
		0,
		0,
		0
	];
	let i = r.toLowerCase(), a = _({
		namespace: da,
		name: "colorToXyz",
		value: i
	}, n), o = h(a);
	if (o !== !1) return [...o.item];
	let s;
	s = i.startsWith("color(") ? G(i, n) : K(i, n);
	let [, ...c] = s;
	return m(a, c), [...c];
}, Da = {
	colorToHex: va,
	colorToHsl: ya,
	colorToHwb: ba,
	colorToLab: xa,
	colorToLch: Sa,
	colorToOklab: Ca,
	colorToOklch: wa,
	colorToRgb: Ta,
	colorToXyz: Ea,
	colorToXyzD50: (e, t = {}) => Ea(e, {
		...t,
		d50: !0
	}),
	numberToHex: _a
}, Oa = {
	cssCalc: U,
	cssVar: pn,
	extractDashedIdent: et,
	isColor: ua,
	isGradient: ca,
	resolveGradient: sa,
	resolveLengthInPixels: nt,
	splitValue: I
};
export { Da as convert, la as resolve, sa as resolveGradient, Oa as utils };
