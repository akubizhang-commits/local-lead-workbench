from leadworkbench.db import Store, now


def test_phone_format_does_not_create_review_or_change(tmp_path):
 s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id']
 with s.connect() as con:
  con.execute('UPDATE companies SET phone=? WHERE id=?',('+1 213-749-0007',cid))
  company=con.execute('SELECT * FROM companies WHERE id=?',(cid,)).fetchone()
  s._observed_field(con,company,'phone','12137490007','https://brand.example','phone',now(),'website')
  assert con.execute('SELECT count(*) FROM reviews WHERE company_id=?',(cid,)).fetchone()[0]==0
  assert con.execute('SELECT count(*) FROM observations WHERE company_id=?',(cid,)).fetchone()[0]>0


def test_old_format_review_resolves_but_real_change_remains(tmp_path):
 s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id']
 with s.connect() as con:
  s._review(con,cid,'field_update','phone','+1 213-749-0007','12137490007','','format')
  s._review(con,cid,'field_update','phone','+1 213-749-0007','12137490008','','changed')
 assert s.resolve_format_reviews()==1
 with s.connect() as con:
  assert con.execute("SELECT count(*) FROM reviews WHERE status='pending'").fetchone()[0]==1


def test_extensions_and_real_digits_are_not_ignored():
 from leadworkbench.company_comparison import equivalent_format
 assert equivalent_format('phone','+1 (213) 749-0007 ext. 108','12137490007 x108')
 assert not equivalent_format('phone','12137490007 ext 108','12137490007108')
 assert not equivalent_format('phone','12137490007 ext 108','12137490007 ext 109')
 assert not equivalent_format('phone','+44 1234567890','+1 1234567890')
 assert not equivalent_format('phone','2137490007','12137490007')
