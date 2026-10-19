const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

function player(page) {
  class Element {
    constructor(tag='div') {this.tagName=tag;this.children=[];this.attrs={};this.handlers={};this.textContent='';}
    setAttribute(k,v){this.attrs[k]=v;}
    addEventListener(k,f){this.handlers[k]=f;}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    querySelectorAll(){return this.children;}
    querySelector(selector){return this.children.find(child=>child.tagName===selector)||null;}
  }
  const ids={};
  for(const id of ['shelf','video','feedback','selected-title','duration','selected-note','watch','previous-track','next-track']) ids[id]=new Element();
  const declaration=page.match(/const tracks=(\[.*?\]);/s);
  const tracks=JSON.parse(declaration[1]);
  tracks.forEach(()=>ids.shelf.children.push(new Element('button')));
  const script=[...page.matchAll(/<script[^>]*>(.*?)<\/script>/gs)].map(m=>m[1]).find(s=>s.includes('const tracks='));
  assert.ok(script);
  const context=vm.createContext({location:{origin:'https://grimiss.github.io'},connectDeckCounter:()=>{},document:{getElementById:id=>ids[id],querySelector:()=>null,createElement:tag=>new Element(tag)}});
  vm.runInContext(script,context);
  return {ids,tracks,run:code=>vm.runInContext(code,context)};
}

test('existing collection and player selection/iframe controls remain intact',()=>{
  const page=fs.readFileSync('listening-room.html','utf8'),p=player(page);
  assert.ok(p.tracks.length>=21);
  assert.equal(new Set(p.tracks.map(t=>t.id)).size,p.tracks.length);
  for(let i=0;i<p.tracks.length;i++){
    p.run(`selectTrack(${i})`);
    assert.equal(p.ids['selected-title'].textContent,p.tracks[i].name);
    assert.equal(p.ids.watch.href,`https://www.youtube.com/watch?v=${p.tracks[i].id}`);
    p.ids.video.children[0].handlers.click();
    const frame=p.ids.video.children[0];
    assert.equal(frame.tagName,'iframe');
    assert.equal(frame.src,`https://www.youtube-nocookie.com/embed/${p.tracks[i].id}?autoplay=1&playsinline=1&rel=0${page.includes('enablejsapi=1')?'&enablejsapi=1&origin=https%3A%2F%2Fgrimiss.github.io':''}`);
    assert.equal(frame.allowFullscreen,true);
    assert.match(frame.allow,/autoplay/);
  }
});

test('inserting a new cassette uses existing player and does not give it Entropy’s note',()=>{
  const page=execFileSync('python',['-c',`from tests.test_sync_youtube import *; print(s.render_update(PAGE,history()+[video()],CONFIG,NOW)[0])`],{encoding:'utf8'});
  const p=player(page);
  assert.equal(p.tracks[0].id,'NEWRELEASE1');
  p.run('selectTrack(0)');
  assert.equal(p.ids['selected-note'].textContent,p.tracks[0].title);
  assert.equal(p.ids.video.children[0].children[0].src,p.tracks[0].thumbnail);
  p.ids.video.children[0].handlers.click();
  assert.match(p.ids.video.children[0].src,/NEWRELEASE1/);
  p.run('selectTrack(1)');
  assert.equal(p.ids.video.children[0].tagName,'button'); // selecting another tape unloads iframe
  assert.match(p.ids['selected-note'].textContent,/fades to black after one hour/);
  const old=fs.readFileSync('tests/fixtures/listening-room.html','utf8');
  assert.equal(page.match(/<head>(.*?)<\/head>/s)[1],old.match(/<head>(.*?)<\/head>/s)[1]);
  assert.equal(page.match(/<header.*?<\/header>/s)[0],old.match(/<header.*?<\/header>/s)[0]);
});

test('player arrows wrap through the collection and preserve preview mode',()=>{
 const p=player(fs.readFileSync('listening-room.html','utf8'));
 p.ids['previous-track'].handlers.click();
 assert.equal(p.ids['selected-title'].textContent,p.tracks.at(-1).name);
 assert.equal(p.ids.video.children[0].tagName,'button');
 p.ids['next-track'].handlers.click();
 assert.equal(p.ids['selected-title'].textContent,p.tracks[0].name);
});
