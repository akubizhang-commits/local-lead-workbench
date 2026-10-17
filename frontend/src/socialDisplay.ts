/** Presentation cleanup only; a generic page title does not invalidate its account. */
export function cleanAccountTitle(value:string|undefined){
 const title=(value||'').trim().replace(/^\(\d+\)\s*/, '');
 if(/^(facebook|instagram|twitter|x)(?:\s*[-|·/]\s*(log in|login|sign up|登录|注册))?$/i.test(title))return '';
 return title.replace(/\s*\(@[^)]+\)\s*[·|–-]\s*Instagram.*$/i,'').replace(/\s*[·|–-]\s*Instagram\s*(?:照片和视频|photos and videos).*$/i,'').trim();
}
