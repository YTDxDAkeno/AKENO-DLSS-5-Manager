"""Read effective, bounded server settings without executing config commands."""
from pathlib import Path
import hashlib
import shlex
from akeno.community.runtime import regular
from akeno.security.downloads import sha256_file


def inspect_config(path):
    root=Path(path).resolve().parent;seen=[];settings={};errors=[]
    def visit(file,stack):
        file=Path(file)
        if len(seen)>=64 or len(stack)>=12:raise ValueError('Server configuration include limit exceeded.')
        regular(file)
        if not file.resolve().is_relative_to(root) or file.stat().st_size>1024*1024:raise ValueError('Server config includes must remain within the supplied folder.')
        if file.resolve() in stack:raise ValueError('Cyclic server configuration include.')
        seen.append({'path':str(file),'sha256':sha256_file(file)})
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            lexer=shlex.shlex(line,posix=True);lexer.whitespace_split=True;lexer.commenters='#'
            words=list(lexer)
            if not words or words[0].startswith('//'):continue
            if words[0].casefold()=='exec':
                if len(words)!=2 or '$' in words[1] or '@' in words[1]:raise ValueError('Unresolved server configuration include.')
                visit(file.parent/words[1].replace('\\','/'),[*stack,file.resolve()]);continue
            if words[0].casefold() in ('set','setr','sets'):words=words[1:]
            if words and words[0].casefold() in ('sv_purelevel','sv_scripthookallowed'):
                if len(words)<2:raise ValueError('Incomplete server policy value.')
                settings[words[0].casefold()]=words[1].casefold()
    try:visit(Path(path),[])
    except (OSError,ValueError) as error:errors.append(str(error))
    pure=settings.get('sv_purelevel');hook=settings.get('sv_scripthookallowed')
    return {'pure_level':int(pure) if pure in ('0','1','2') and not errors else None,
        'script_hook_allowed':hook in ('true','1') if hook in ('true','false','0','1') and not errors else None,
        'files':seen,'errors':errors,'complete':not errors,'path':str(path)}
