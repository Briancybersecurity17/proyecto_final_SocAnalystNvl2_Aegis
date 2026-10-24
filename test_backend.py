import sys
import os
sys.path.append(os.path.abspath('backend'))
from main import app
from fastapi.testclient import TestClient

client = TestClient(app)
file_path = r'C:/Users/Brian/Downloads/SOC_L2_Logs_Ampliados_Multiples_Incidentes.xlsx'
with open(file_path, 'rb') as f:
    response = client.post('/api/analyze', files={'file': ('SOC.xlsx', f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
print('Status code:', response.status_code)
print('JSON:', response.json())
