"""联动适配矩阵页面，仅供可见页面读取。"""
from source.common import *

class Site(Provider):
    def initial(self):return {'revision':{'c1':1,'c2':1},'extra':False}
    def event(self,d):
        if d['event']=='correct':self.state['revision'][d.get('card','c1')]+=1
        elif d['event']=='framework':self.state['extra']=True
        else:return Reply({'error':'unknown_event'},400)
        return {'ok':True}
    def options(self,c):
        return {'drivers':['d10','d9'] if c=='c1' else ['stable'],'frameworks':['infer','train']+(['next'] if self.state['extra'] else [])}
    def get(self,p,q,h):
        if p=='/':
            return web(page('算力适配门户','''<label>卡型<select id="card"><option>c1</option><option>c2</option></select></label><label>驱动<select id="driver"></select></label><label>框架<select id="framework"></select></label><label>精度<select id="precision"><option>fp16</option><option>int8</option></select></label><div id="loading"></div><section id="summary"></section><a id="detail">测试详情</a>''', '''
const card=document.querySelector('#card'),driver=document.querySelector('#driver'),framework=document.querySelector('#framework'),precision=document.querySelector('#precision');let epoch=0;
async function branches(){let e=++epoch;document.querySelector('#loading').textContent='选项更新中';let r=await fetch('/ui/options?card='+card.value);let d=await r.json();await new Promise(r=>setTimeout(r,160));if(e!==epoch)return;driver.innerHTML=d.drivers.map(x=>'<option>'+x+'</option>').join('');framework.innerHTML=d.frameworks.map(x=>'<option>'+x+'</option>').join('');await result();}
async function result(){let e=++epoch;let params=new URLSearchParams({card:card.value,driver:driver.value,framework:framework.value,precision:precision.value});document.querySelector('#loading').textContent='结果更新中';let text=await(await fetch('/ui/result?'+params)).text();await new Promise(r=>setTimeout(r,120));if(e!==epoch)return;document.querySelector('#summary').innerHTML=text;document.querySelector('#detail').href='/detail?'+params;document.querySelector('#loading').textContent='已完成';}
card.onchange=branches;driver.onchange=result;framework.onchange=result;precision.onchange=result;branches();'''))
        if p=='/ui/options':return self.options(q['card'])
        if p in ['/ui/result','/detail']:
            c=q['card'];o=self.options(c)
            if q['driver'] not in o['drivers'] or q['framework'] not in o['frameworks']:return web('选项不属于当前卡型',409)
            rev=self.state['revision'][c];identity='|'.join(q[k] for k in ['card','driver','framework','precision'])
            status='尚未测' if q['framework']=='next' else ('不支持' if q['precision']=='int8' and q['framework']=='train' else '通过')
            sub='失败' if rev>1 and c=='c1' else status
            build={'d10':'10.12.3+build7','d9':'9.10.9+build12','stable':'4.2.0+build5'}[q['driver']]
            body='<p id="identity">'+escaped(identity)+'</p><p id="revision">修订 '+str(rev)+'</p><p id="status">'+status+'</p>'
            if p=='/detail':body+='<p id="build">构建 '+build+'</p><p>条件：模型 m-demo；批量 4；算子 attention <b id="operator">'+sub+'</b></p><a id="original" href="/original?card='+c+'&revision='+str(rev)+'&identity='+identity+'">下载原始记录</a>'
            return web(page('测试详情',body)) if p=='/detail' else web(body)
        if p=='/original':
            raw=dump({'combination':q['identity'],'card':q['card'],'revision':int(q['revision']),'synthetic':True}).encode()
            return Reply(raw,mime='application/octet-stream',headers={'Content-Disposition':'attachment; filename="test-result.bin"','X-Content-SHA256':digest(raw)})
        return super().get(p,q,h)

if __name__=='__main__':main(Site)
