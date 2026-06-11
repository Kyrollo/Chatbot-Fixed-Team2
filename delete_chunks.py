import asyncio
import asyncpg

async def run():
    conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5432/domain_db')
    result = await conn.execute("DELETE FROM document_chunks WHERE domain_id = 'e9012d14-29ae-498c-a35c-25de81b978cc'")
    print('Done:', result)
    await conn.close()

asyncio.run(run())