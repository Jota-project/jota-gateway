"use strict";
const backgroundPositionX = require("../jsdom/living/css/properties/backgroundPositionX.js");
const backgroundPositionY = require("../jsdom/living/css/properties/backgroundPositionY.js");
const borderBottomWidth = require("../jsdom/living/css/properties/borderBottomWidth.js");
const borderLeftWidth = require("../jsdom/living/css/properties/borderLeftWidth.js");
const borderRightWidth = require("../jsdom/living/css/properties/borderRightWidth.js");
const borderTopWidth = require("../jsdom/living/css/properties/borderTopWidth.js");
const fontWeight = require("../jsdom/living/css/properties/fontWeight.js");

module.exports = new Map([
  ["background-position-x", backgroundPositionX.resolveComputedValue],
  ["background-position-y", backgroundPositionY.resolveComputedValue],
  ["border-bottom-width", borderBottomWidth.resolveComputedValue],
  ["border-left-width", borderLeftWidth.resolveComputedValue],
  ["border-right-width", borderRightWidth.resolveComputedValue],
  ["border-top-width", borderTopWidth.resolveComputedValue],
  ["font-weight", fontWeight.resolveComputedValue]
]);
