/* Exercise the same server-rendered source delivered by app.Handler. */
const {execFileSync} = require('node:child_process');
module.exports = (filename, route = '/') => execFileSync('python3', ['-c',
  'from pathlib import Path; import seo,sys; print(seo.render(Path(sys.argv[1]).read_text(),sys.argv[2],"tabobooks.com",sys.argv[1]))',
  filename, route], {encoding:'utf8'});
