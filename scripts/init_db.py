from app.database.mongo import mongo

if __name__ == "__main__":
    mongo.ensure_indexes()
    print("MongoDB indexes created")
