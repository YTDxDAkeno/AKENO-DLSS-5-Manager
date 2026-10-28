"""After approval only: password input via controlling terminal; JSON goes directly to a pipe, never a file."""
import getpass,json,sys
if sys.stdout.isatty():raise SystemExit('REFUSED: stdout must be a direct pipe to the staging seed command')
values={}
for field,label in [('pc_a_password','PC A'),('pc_b_password','PC B')]:
    a=getpass.getpass(label+' staging password (16–128 characters): ')
    b=getpass.getpass('Repeat: ')
    if a!=b or not 16<=len(a)<=128:raise SystemExit('Mismatch or invalid length; nothing sent')
    values[field]=a
sys.stdout.write(json.dumps(values));sys.stdout.flush()
