"use strict";
const backgroundPositionX = require("../jsdom/living/css/properties/backgroundPositionX.js");
const backgroundPositionY = require("../jsdom/living/css/properties/backgroundPositionY.js");
const borderBottomWidth = require("../jsdom/living/css/properties/borderBottomWidth.js");
const borderLeftWidth = require("../jsdom/living/css/properties/borderLeftWidth.js");
const borderRightWidth = require("../jsdom/living/css/properties/borderRightWidth.js");
const borderTopWidth = require("../jsdom/living/css/properties/borderTopWidth.js");

module.exports = new Map([
  ["background-position-x", backgroundPositionX.getResolvedValue],
  ["background-position-y", backgroundPositionY.getResolvedValue],
  ["border-bottom-width", borderBottomWidth.getResolvedValue],
  ["border-left-width", borderLeftWidth.getResolvedValue],
  ["border-right-width", borderRightWidth.getResolvedValue],
  ["border-top-width", borderTopWidth.getResolvedValue]
]);
