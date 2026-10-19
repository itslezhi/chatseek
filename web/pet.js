'use strict';
// Independent facial motion, a readable body silhouette, and short idle gestures.
(() => {
  const host=document.getElementById('pet'),rig=document.getElementById('pet-rig');
  const gaze=document.getElementById('pet-gaze'),left=document.getElementById('pet-eye-left');
  const right=document.getElementById('pet-eye-right'),smile=document.getElementById('pet-smile');
  const grip=document.getElementById('pet-grip');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const small=matchMedia('(max-width:720px)');
  const target={x:0,y:0},position={x:0,y:0};
  let frame=0,lastFrame=0,lastPointer=0,hasPointer=false;
  let blinkAt=0,nextBlink=performance.now()+1800+Math.random()*2200;
  let doubleBlink=false,isSecondBlink=false;
  let reactionAt=0,nextIdle=performance.now()+1600+Math.random()*1200;
  let action=null,firstLookPending=true;
  const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
  const randomBlinkDelay=()=>2600+Math.random()*4800;
  function activity(name){if(host.dataset.activity!==name)host.dataset.activity=name;}
  function openEyes(){
    left.setAttribute('transform','translate(468 627) rotate(9)');
    right.setAttribute('transform','translate(793 680) rotate(9)');
  }
  function reset(){
    rig.style.transform='none';gaze.removeAttribute('transform');smile.removeAttribute('transform');openEyes();activity('paused');
  }
  function visible(){return !host.hidden && !document.hidden && !reduced.matches;}
  function animate(now){
    frame=0;if(!visible()){reset();return;}
    const elapsed=Math.min((now-(lastFrame||now))/1000,.05);lastFrame=now;
    const dragging=host.classList.contains('dragging');
    const tracking=hasPointer && now-lastPointer<1800 && !dragging;
    const responding=Boolean(document.querySelector('#conversation .thinking'));
    let aimX=0,aimY=0,gestureRoll=0,gestureLift=0,stretch=0;
    if(tracking || dragging){action=null;nextIdle=Math.max(nextIdle,now+900);}
    if(!tracking && !dragging && !action && now>=nextIdle){
      const types=['look-around','look-around','wiggle','nod','stretch'];
      const type=firstLookPending?'look-around':types[Math.floor(Math.random()*types.length)];
      firstLookPending=false;
      action={type,start:now,duration:type==='look-around'?3600:2200,direction:Math.random()<.5?-1:1};
    }
    if(action){
      const p=clamp((now-action.start)/action.duration,0,1),envelope=Math.sin(p*Math.PI);
      if(action.type==='look-around'){
        aimX=Math.sin(p*Math.PI*2)*.95*action.direction;
        aimY=-.2*envelope;
        gestureLift=-Math.sin(p*Math.PI)*3;
      }else if(action.type==='wiggle'){
        aimX=Math.sin(p*Math.PI*2)*.65*action.direction;
        gestureRoll=Math.sin(p*Math.PI*5)*4*envelope;
        gestureLift=-Math.abs(Math.sin(p*Math.PI*2))*9;
      }else if(action.type==='nod'){
        aimX=action.direction*.25*envelope;aimY=Math.sin(p*Math.PI*4)*.75*envelope;
        gestureLift=-Math.abs(Math.sin(p*Math.PI*2))*6;
      }else{
        aimY=-.25*envelope;stretch=envelope*.035;gestureLift=-envelope*10;
      }
      activity(action.type);
      if(p>=1){action=null;nextIdle=now+2200+Math.random()*2800;}
    }else if(tracking){aimX=target.x;aimY=target.y;activity('following');}
    else if(dragging){activity('dragging');}
    else{aimX=Math.sin(now/2600)*.22;aimY=Math.sin(now/3400)*.12;activity('idle');}
    // Easing is based on elapsed time, so high-refresh screens behave consistently.
    const ease=1-Math.exp(-elapsed*6.5);
    position.x+=(aimX-position.x)*ease;position.y+=(aimY-position.y)*ease;
    const reaction=clamp((now-reactionAt)/800,0,1);
    const perk=reactionAt && reaction<1?Math.sin(reaction*Math.PI)*10:0;
    const breathing=dragging?0:Math.sin(now/950)*.018;
    const float=dragging?0:Math.sin(now/850)*4.8;
    const roll=dragging?0:Math.sin(now/1900)*1.6+position.x*6+gestureRoll;
    const listening=responding?Math.sin(now/400)*1.4:0;
    const scaleX=1-breathing*.5-stretch*.55,scaleY=1+breathing+stretch;
    const travel=small.matches ? .75 : 1;
    rig.style.transform=`perspective(550px) translate3d(${(position.x*8*travel).toFixed(3)}px,${((float+gestureLift-perk+listening)*travel).toFixed(3)}px,0) rotateX(${(-position.y*14).toFixed(3)}deg) rotateY(${(position.x*23).toFixed(3)}deg) rotateZ(${roll.toFixed(3)}deg) scale(${scaleX.toFixed(4)},${scaleY.toFixed(4)})`;
    // Move facial features across the body, rather than just turning a flat sticker.
    gaze.setAttribute('transform',`translate(${(position.x*95).toFixed(2)} ${(position.y*62).toFixed(2)})`);
    if(!blinkAt && now>=nextBlink && !dragging){blinkAt=now;if(!isSecondBlink)doubleBlink=Math.random()<.2;}
    let eyelid=1;
    if(blinkAt){
      const age=now-blinkAt;
      if(age<65)eyelid=1-.94*Math.sin(age/65*Math.PI/2);
      else if(age<100)eyelid=.06;
      else if(age<225)eyelid=.06+.94*(1-Math.cos((age-100)/125*Math.PI/2));
      else{
        blinkAt=0;
        if(doubleBlink && !isSecondBlink){nextBlink=now+110;isSecondBlink=true;}
        else{nextBlink=now+randomBlinkDelay();isSecondBlink=false;doubleBlink=false;}
      }
    }
    left.setAttribute('transform',`translate(468 627) rotate(${(9+position.y*3).toFixed(2)}) scale(1 ${eyelid.toFixed(3)})`);
    right.setAttribute('transform',`translate(793 680) rotate(${(9-position.y*3).toFixed(2)}) scale(1 ${eyelid.toFixed(3)})`);
    smile.setAttribute('transform',`translate(${(position.x*-18).toFixed(2)} ${(breathing*-150).toFixed(2)})`);
    frame=requestAnimationFrame(animate);
  }
  function sync(){
    if(!visible()){if(frame)cancelAnimationFrame(frame);frame=0;lastFrame=0;blinkAt=0;action=null;reset();return;}
    if(!frame){lastFrame=0;firstLookPending=true;const now=performance.now();nextBlink=now+1800+Math.random()*2400;nextIdle=now+1600+Math.random()*1200;frame=requestAnimationFrame(animate);}
  }
  addEventListener('pointermove',event=>{
    if(event.pointerType==='touch' || !visible() || host.classList.contains('dragging'))return;
    const rect=host.getBoundingClientRect();
    target.x=clamp((event.clientX-rect.left-rect.width*.5)/Math.max(innerWidth*.42,230),-1,1);
    target.y=clamp((event.clientY-rect.top-rect.height*.48)/Math.max(innerHeight*.32,190),-1,1);
    hasPointer=true;firstLookPending=true;lastPointer=performance.now();
  },{passive:true});
  function rest(){hasPointer=false;target.x=0;target.y=0;}
  document.documentElement.addEventListener('pointerleave',rest);addEventListener('blur',rest);
  grip.addEventListener('pointerdown',()=>{rest();reactionAt=0;});
  grip.addEventListener('pointerup',()=>{reactionAt=performance.now();});
  grip.addEventListener('focus',()=>{if(visible())reactionAt=performance.now();});
  document.addEventListener('visibilitychange',sync);reduced.addEventListener('change',sync);
  const observer=new MutationObserver(sync);observer.observe(host,{attributes:true,attributeFilter:['hidden']});
  addEventListener('pagehide',()=>{if(frame)cancelAnimationFrame(frame);frame=0;observer.disconnect();});
  addEventListener('pageshow',()=>{observer.observe(host,{attributes:true,attributeFilter:['hidden']});sync();});sync();
})();
