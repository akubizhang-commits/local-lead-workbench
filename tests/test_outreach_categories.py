from leadworkbench.db import Store


def test_exclusive_counts_exclude_jobs_and_prioritize_buyers(tmp_path):
 s=Store(tmp_path);c=s.manual_company('Brand','https://brand.example')
 s.apply_enrichment(c['id'],{'status':'completed','contacts':[{'name':'Alice Example','email':'alice@brand.example','kind':'procurement','role_status':'explicit','role':'Buyer','source_url':'https://brand.example/team','evidence':'Alice Example Buyer'}]})
 with s.connect() as con:
  template=dict(con.execute('SELECT * FROM contacts WHERE company_id=?',(c['id'],)).fetchone())
  for i,(email,kind,role) in enumerate([('alice@brand.example','general',''),('info@brand.example','general',''),('jobs@brand.example','general',''),('info@brand.example','general','Sales')]):
   row={**template,'id':str(i),'fingerprint':str(i),'email':email,'name':'','kind':kind,'role':role,'role_status':'unknown'}
   con.execute('INSERT INTO contacts VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
 s.add_social(c['id'],'https://instagram.com/brand/','test')
 row=s.company_page()['items'][0]
 assert row['procurement_count']==1
 assert row['email_count']==1
 assert row['social_count']==1
 assert s.company_page(contact='email')['total']==1
 groups=s.detail(c['id'])['contacts']
 assert len([g for g in groups if g.get('outreach_category')=='email'])==1
 assert any(g.get('outreach_category')=='irrelevant' for g in groups)


def test_recruitment_matching_is_conservative():
 from leadworkbench.contact_projection import outreach_category
 # Name/surname substrings do not prove recruitment purpose.
 assert outreach_category('general','unknown','brandjobs@gmail.example.org','')=='email'
 assert outreach_category('general','unknown','jobs@brand.example.com','')=='irrelevant'
 assert outreach_category('general','unknown','hello@jobs-fashion.example.org','')=='email'
 assert outreach_category('general','unknown','chris@brand.example.com','')=='email'
 assert outreach_category('general','unknown','care@brand.example.com','')=='email'
 assert outreach_category('procurement','explicit','buy@brand.example.com','Buyer')=='procurement'
