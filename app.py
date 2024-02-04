# app.py
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
from datetime import timedelta
from flask_jwt_extended import create_access_token
from collections import defaultdict
import os 
app = Flask(__name__)

# global vars to change websocket listeners and etc
subscriptions = defaultdict(int)
WEB_SOCKET = None

# Replace these values with your MySQL connection details
# mysql_host = "localhost"
# mysql_user = "user"
# mysql_password = "password"
# mysql_database = "tanX_alert"

# # Construct the MySQL connection URI
# mysql_uri = f"mysql+mysqlconnector://{mysql_user}:{mysql_password}@{mysql_host}/{mysql_database}"

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']

# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_jwt_secret_key'

# Flask-JWT configuration
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=1)  # Token expiration time

jwt = JWTManager(app)

db = SQLAlchemy(app)

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
# use binance to get the price of the coins after any changes or updates


# Define your User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(100), nullable=False)

# Define your Alert model
class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    coin = db.Column(db.String(10), nullable=False)
    target_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='created')  # You can add more status types as needed

def getActiveAlerts():
    return Alert.query.filter(Alert.status == 'created') 


# Sign up route
@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()

    new_user = User(username=data['username'], password=data['password'])
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User signed up successfully'}), 201


# Login route
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(username=data['username']).first()

    if user and user.password == data['password']:
        # Import create_access_token
        from flask_jwt_extended import create_access_token
        
        # Use create_access_token to create a new JWT token
        access_token = create_access_token(identity=user.id)
        return jsonify({'message': 'Login successful', 'access_token': access_token}), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401


# API endpoint to create/update an alert
@app.route('/alerts/create', methods=['POST'])
@jwt_required()
def create_alert():
    data = request.get_json()

    if 'coin' not in data or 'target_price' not in data:
        return jsonify({'message': 'Missing required fields (coin and target_price)'}), 400

    current_user_id = get_jwt_identity()

    # Check if the user already has an alert for the specified coin
    existing_alert = Alert.query.filter_by(user_id=current_user_id, coin=data['coin']).first()

    if existing_alert:
        # Update the status to 'created' if the alert was deleted
        if existing_alert.status == 'deleted':
            existing_alert.status = 'created'
            db.session.commit()
            return jsonify({'message': 'Alert updated successfully'}), 200
        else:
            return jsonify({'message': 'You already have an alert for this coin'}), 400

    new_alert = Alert(user_id=current_user_id, coin=data['coin'], target_price=data['target_price'])
    db.session.add(new_alert)
    db.session.commit()

    unsubscribeFromSocket(list(subscriptions.keys()))   
    subscriptions[data["coin"].lower() + "usdt@kline_1m"] += 1

    print("\t\tNew subscriptions : ", subscriptions)

    sendToSocket(list(subscriptions.keys()))

    return jsonify({'message': 'Alert created successfully'}), 201

# API endpoint to mark delete an alert
@app.route('/alerts/delete/<int:alert_id>', methods=['DELETE'])
@jwt_required()
def delete_alert(alert_id):
    current_user_id = get_jwt_identity()
    alert = Alert.query.filter_by(id=alert_id, user_id=current_user_id).first()

    if alert:
        # Instead of deleting, update the status to 'deleted'
        alert.status = 'deleted'
        db.session.commit()
        return jsonify({'message': 'Alert marked as deleted'}), 200
    else:
        return jsonify({'message': 'Alert not found or unauthorized'}), 404

# API endpoint to delete row an alert
@app.route('/alerts/delete/real/<int:alert_id>', methods=['DELETE'])
@jwt_required()
def delete_alert_deleteRow(alert_id):
    current_user_id = get_jwt_identity()
    alert = Alert.query.filter_by(id=alert_id, user_id=current_user_id).first()

    if alert:
        db.session.delete(alert)
        db.session.commit()
        return jsonify({'message': 'Alert deleted successfully'})
    else:
        return jsonify({'message': 'Alert not found or unauthorized'}), 404

# API endpoint to fetch alerts with filters and pagination
@app.route('/alerts', methods=['GET'])
@jwt_required()
def get_user_alerts():
    current_user_id = get_jwt_identity()

    # Pagination parameters (page and per_page)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=10, type=int)

    # Filter alerts based on status, if provided
    status_filter = request.args.get('status', type=str)

    # Query alerts based on user_id, optional status filter, and excluding 'deleted' alerts
    alerts_query = Alert.query.filter_by(user_id=current_user_id, status='created' if not status_filter else status_filter)

    # Paginate the results
    alerts = alerts_query.paginate(page=page, per_page=per_page, error_out=False)

    if not alerts.items:
        return jsonify({'message': 'No alerts found for the current user'}), 404

    alert_list = [{
        'id': alert.id,
        'coin': alert.coin,
        'target_price': alert.target_price,
        'status': alert.status
    } for alert in alerts.items]

    # Include pagination information in the response headers
    response_headers = {
        'X-Total-Count': alerts.total,
        'X-Total-Pages': alerts.pages,
        'X-Current-Page': alerts.page,
        'X-Per-Page': per_page
    }


    return jsonify({'alerts': alert_list}), 200, response_headers

# API endpoint to fetch alerts based on status
@app.route('/alerts/<status>', methods=['GET'])
@jwt_required()
def get_user_alerts_by_status(status):
    current_user_id = get_jwt_identity()

    # Check if the provided status is valid
    valid_statuses = ['created', 'deleted', 'triggered']  # Add more statuses as needed
    if status not in valid_statuses:
        return jsonify({'message': 'Invalid status provided'}), 400

    # Fetch alerts based on user ID and status
    alerts = Alert.query.filter_by(user_id=current_user_id, status=status).all()

    if not alerts:
        return jsonify({'message': f'No {status} alerts found for the current user'}), 404

    alert_list = [{
        'id': alert.id,
        'coin': alert.coin,
        'target_price': alert.target_price,
        'status': alert.status
    } for alert in alerts]

    return jsonify({'alerts': alert_list})



# web socket definition
import websocket
import json
import threading
import time

SOCK_URL = f"wss://stream.binance.com/ws"

def unsubscribeFromSocket(lst):
    WEB_SOCKET.send(
        json.dumps({"method": "UNSUBSCRIBE", "params": lst, "id": 312})
    )

def sendToSocket(lst):
    WEB_SOCKET.send(
        json.dumps({"method": "SUBSCRIBE", "params": lst, "id": 1})
    )

import smtplib

#for normal email connection
def connect_to_smtp_server():
    try:
        s = smtplib.SMTP('smtp.office365.com', 587)
        s.starttls()
        s.login("cointargetalert@outlook.com", "Outlook99")
        return s
    except smtplib.SMTPException as e:
        # Handle connection/authentication errors here
        print("SMTP Connection Error:", str(e))
        return None


def send_email(user_email, coin_name,smtp_email_obj):
    SUBJECT = f'Target Alert'
    TEXT = f'Dear User, \n The coin {coin_name} that you set for alert has reached its target.\n Thank you.'
    message = 'Subject: {}\n\n{}'.format(SUBJECT, TEXT)
    smtp_email_obj.sendmail("cointargetalert@outlook.com", user_email, message)
    smtp_email_obj.quit()

s = connect_to_smtp_server()


def on_message(ws, message):
    data = json.loads(message)  

    # print(data)

    reqMsg = {"coin" : data["s"][:-4].upper(), "price" : float(data["k"]["c"])}
    
    with app.app_context():
        print(reqMsg)

        satisfyingAlerts = Alert.query.filter(Alert.status == 'created', Alert.coin == reqMsg["coin"], 
                        Alert.target_price <= reqMsg["price"]).all()
        

        # get user emails using uid from satisfying alerts
        userDetails = []

        oldSubscriptions = subscriptions.copy()

        for alert in satisfyingAlerts:
            userDetails.append({ 'email': User.query.filter(User.id == alert.user_id).first().email
                , "coin" : reqMsg["coin"], "price" : reqMsg["coin"] }) 
            
            key = reqMsg["coin"].lower() + "usdt@kline_1m"
            if subscriptions[key] == 1:
                del subscriptions[key]
            else:
                subscriptions[key] -= 1

            alert.status = 'triggered'


            # TODO : use userDetails to send email to the user using rabbiMQ
            #simple method for sending the mail to the user when the target is found
            first_dict = userDetails[0]
            email_value = first_dict['email']
            coin_name = first_dict['coin']
            #s = connect_to_smtp_server()
            send_email(email_value, coin_name,s)
            print("\t\tTriggered : ", userDetails)
        
        if len(satisfyingAlerts):
            db.session.commit()
        
        if oldSubscriptions.keys() != subscriptions.keys():
            unsubscribeFromSocket(list(oldSubscriptions.keys()))
            sendToSocket(list(subscriptions.keys()))


def on_close(ws, close_status_code, close_msg):
    print("### closed ###")

def on_open(ws):
    print("### open ###")
    
def on_error(ws, error):
    print(error)


if __name__ == '__main__':

    # websocket.enableTrace(True)
    WEB_SOCKET = websocket.WebSocketApp(SOCK_URL, on_open=on_open, on_close=on_close, on_message=on_message, on_error=on_error)
    
    wsThread = threading.Thread(target=WEB_SOCKET.run_forever)
    wsThread.daemon = True
    wsThread.start()

    conn_timeout = 5
    while not WEB_SOCKET.sock.connected and conn_timeout:
        time.sleep(1)
        conn_timeout -= 1


    with app.app_context():
        db.create_all()

        alerts = Alert.query.filter(Alert.status == 'created').all()

        for alert in alerts:
            subscriptions[alert.coin.lower() + "usdt@kline_1m"] += 1
        
        print("\t", subscriptions)

        sendToSocket(list(subscriptions.keys()))
    
    app.run(debug=True, host='0.0.0.0')

