import requests

def test_dashboard_session_flow():
    s = requests.Session()
    r = s.post('http://127.0.0.1:8080/api/auth/session/login', json={'username': 'admin', 'password': 'admin123'}, timeout=5)
    assert r.status_code == 200, r.text
    
    r_me = s.get('http://127.0.0.1:8080/api/auth/session/me', timeout=5)
    assert r_me.status_code == 200, r_me.text
    assert r_me.json().get('username') == 'admin'

if __name__ == '__main__':
    test_dashboard_session_flow()
    print('Dashboard session integration self-check passed!')
