'use strict';
function quote(total, rate) {
  return Math.round(total * (1 - rate));
}
module.exports = { quote };
