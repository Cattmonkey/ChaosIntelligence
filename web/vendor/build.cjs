'use strict';
// Run npm ci --ignore-scripts in an isolated copy of this directory, then node build.cjs.
require('esbuild').buildSync({entryPoints:[require('path').join(__dirname,'entry.js')],bundle:true,format:'iife',globalName:'ChaosCM',target:'es2020',minify:true,legalComments:'inline',outfile:require('path').join(__dirname,'codemirror.js')});
