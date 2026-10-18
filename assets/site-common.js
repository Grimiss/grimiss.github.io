(()=>{
 const toggle=document.querySelector('.site-menu'),nav=document.getElementById('navigation');
 if(toggle&&nav){
  const close=()=>{nav.classList.remove('is-open');toggle.setAttribute('aria-expanded','false');};
  toggle.addEventListener('click',()=>{const open=nav.classList.toggle('is-open');toggle.setAttribute('aria-expanded',String(open));});
  nav.querySelectorAll('a').forEach(a=>a.addEventListener('click',close));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&toggle.getAttribute('aria-expanded')==='true'){close();toggle.focus();}});
  document.addEventListener('click',e=>{if(!e.target.closest('.site-header'))close();});
  matchMedia('(min-width:801px)').addEventListener('change',close);
 }
 const year=document.getElementById('year');if(year)year.textContent=new Date().getFullYear();
 const motion=matchMedia('(prefers-reduced-motion: reduce)');
 function applyMotion(){
  if(!motion.matches)return;
  document.querySelectorAll('video[autoplay]').forEach(v=>{v.autoplay=false;v.pause();});
 }
 applyMotion();motion.addEventListener('change',applyMotion);
 // Keep looping backgrounds paused while this tab is hidden.
 let resume=[];
 document.addEventListener('visibilitychange',()=>{
  if(document.hidden){resume=Array.from(document.querySelectorAll('video')).filter(v=>!v.paused);resume.forEach(v=>v.pause());}
  else{if(!motion.matches)resume.forEach(v=>v.play().catch(()=>{}));resume=[];}
 });
})();