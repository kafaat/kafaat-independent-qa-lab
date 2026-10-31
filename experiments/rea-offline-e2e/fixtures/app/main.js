'use strict';
// Pure static fixture: the REA parser must not execute this program.
const { quote } = require('./pricing');
module.exports = { checkout: (total) => quote(total, 0.1) };
