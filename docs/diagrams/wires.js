// wire(from, to, label, fromSide, toSide, {both, t}) draws a curved arrow between two elements.
if(location.hash=="#dark")document.documentElement.classList.add("dark");
const W=[];function wire(a,b,label,sa="b",sb="t",o={}){W.push([a,b,label,sa,sb,o])}
function pt(el,side,root){const r=el.getBoundingClientRect(),R=root.getBoundingClientRect(),x=r.left-R.left,y=r.top-R.top;
  return{t:[x+r.width/2,y],b:[x+r.width/2,y+r.height],l:[x,y+r.height/2],r:[x+r.width,y+r.height/2]}[side]}
function draw(){const root=document.getElementById("d"),ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");
  svg.setAttribute("class","wires");svg.innerHTML=`<defs><marker id="h" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M1 1L9 5L1 9" fill="none" stroke="var(--muted)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>`;
  root.prepend(svg);
  for(const[a,b,label,sa,sb,o]of W){const p=pt(document.getElementById(a),sa,root),q=pt(document.getElementById(b),sb,root);q[1]+=o.dy||0;q[0]+=o.dx||0;
    const v={t:[0,-1],b:[0,1],l:[-1,0],r:[1,0]},k=Math.max(40,Math.hypot(q[0]-p[0],q[1]-p[1])*.45);
    const c1=[p[0]+v[sa][0]*k,p[1]+v[sa][1]*k],c2=[q[0]+v[sb][0]*k,q[1]+v[sb][1]*k];
    const path=document.createElementNS(ns,"path");
    path.setAttribute("d",`M${p} C${c1} ${c2} ${q}`);path.setAttribute("fill","none");path.setAttribute("stroke","var(--line)");
    path.setAttribute("stroke-width","1.8");path.setAttribute("marker-end","url(#h)");if(o.both)path.setAttribute("marker-start","url(#h)");
    svg.appendChild(path);
    if(label){const L=path.getTotalLength(),m=path.getPointAtLength(L*(o.t??.5)),d=document.createElement("div");
      d.className="lbl";d.innerHTML=label;d.style.left=m.x+"px";d.style.top=m.y+"px";root.appendChild(d)}}}
document.fonts.ready.then(()=>requestAnimationFrame(draw));
