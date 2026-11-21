#!/usr/bin/env python3
"""Builds the live demos (docs/demo/wall.html, overview.html) from the real Parent page (../parent.html) with SAMPLE data.
Run after changing parent.html:  python3 docs/build_demo.py"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRE = """<script>
/* DEMO: sample data only. Weekends show a weekday at 10:15 so the demo always has lessons "now". */
(function(){const R=Date,n=new R();let off=0;const wd=n.getDay();if(wd===6)off=2*864e5;else if(wd===0)off=864e5;
 const t=new R(n.getTime()+off);t.setHours(10,15,0,0);off=t.getTime()-n.getTime();
 class D extends R{constructor(...a){a.length?super(...a):super(R.now()+off)}static now(){return R.now()+off}}window.Date=D;})();
window.SSC_NATIVE=true;window.SSC_WALL=%WALL%;
</script>
"""
POST = """<script>
(function(){
 const pad=n=>String(n).padStart(2,"0"),iso=d=>d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate());
 const now=new Date(),day=n=>{const d=new Date(now);d.setDate(d.getDate()+n);return d};
 const monday=day(-((now.getDay()+6)%7));
 const slots=[["08:00","08:50"],["08:55","09:45"],["10:00","10:50"],["10:55","11:45"],["11:50","12:40"]];
 const plan=[["M","HU","R201"],["D","KA","R105"],["E","BL","R112"],["B","MA","Lab 2"],["PE","SO","Gym"]];
 const week={};
 for(let w=0;w<2;w++){const ms=day(-((now.getDay()+6)%7)+7*w);week[iso(ms)]=[];
  for(let i=0;i<5;i++){const d=new Date(ms);d.setDate(d.getDate()+i);
   slots.forEach((s,k)=>{const p=plan[(k+i)%5];const cancelled=(w===0&&i===((now.getDay()+6)%7)&&k===3);
    week[iso(ms)].push({date:iso(d),start:iso(d)+"T"+s[0],end:iso(d)+"T"+s[1],subject:p[0],teacher:p[1],room:p[2],cancelled,info:cancelled?"Teacher ill":""});});}}
 const hw=(o,s,t,st)=>({subject:s,due:iso(day(o)),text:t,remark:"",done:false,status:st,days_left:o});
 const data={sections:["today","timetable","homework","exams","plan","changes"],synced_at:Date.now()/1000-420,server_time:Date.now()/1000,
  timetable:week,
  homework:[hw(-1,"D","Finish the essay on the short story","missed"),hw(1,"E","Learn vocabulary unit 4","soon"),hw(2,"M","Exercises p. 44, 1-6","soon"),hw(6,"B","Prepare the cell biology talk","pending")],
  exams:[{date:iso(day(4)),start:"10:00",end:"10:50",subject:"M",text:"Test: functions"},{date:iso(day(12)),start:"",end:"",subject:"E",text:"Listening test"}],
  plan:[{date:iso(now),start:"16:30",minutes:45,subject:"E",title:"Vocabulary unit 4",notes:"flashcards",done:false},
        {date:iso(now),start:"",minutes:30,subject:"M",title:"Maths p. 44",notes:"",done:true},
        {date:iso(day(1)),start:"17:00",minutes:60,subject:"B",title:"Slides for the talk",notes:"",done:false}],
  changes:[{at:Date.now()/1000-3600,kind:"timetable",text:"Cancelled: B on "+iso(now).slice(5)+" 10:55"},{at:Date.now()/1000-7200,kind:"homework",text:"New homework: M - Exercises p. 44"},{at:Date.now()/1000-9000,kind:"timetable",text:"Room change: E 10:00: R112 -> R118"}]};
 window.SSC_update(data,false,Date.now());
})();
</script>
"""

src = (ROOT.parent / "parent.html").read_text(encoding="utf-8")
for name, wall in (("wall", "true"), ("overview", "false")):
    html = src.replace("<head>", "<head>\n" + PRE.replace("%WALL%", wall), 1)
    html = html.replace("</body>", POST + "</body>", 1)
    html = html.replace("<title>School overview</title>", "<title>SSC demo (sample data)</title>")
    (ROOT / "demo" / f"{name}.html").write_text(html, encoding="utf-8")
    print("wrote", f"docs/demo/{name}.html")
